#!/usr/bin/env python3
"""Single public ingress for the governed Issue-agent Railway service.

Railway exposes exactly one public domain per service, routed to ``$PORT``.
The governed trigger must reach two logically separate internal authorities
through it, in order: the trusted provisioning edge
(``POST /issue-agent/provision``) and then the read-only execution issuer
(``POST /issue-agent/authorize``). Before this ingress existed, ``$PORT`` was
bound directly by the issuer, so the provisioning POST reached the issuer and
was answered 404: the provisioner listened on a port nothing public routed to.

This process owns ``$PORT`` and nothing else. It is a fixed route table, not a
proxy:

-   exactly four routes are served: ``GET /healthz``,
    ``POST /issue-agent/provision``, ``POST /issue-agent/authorize`` and the
    read-only ``GET /issue-agent/status/<authorization_id>`` (the issuer's
    non-secret execution status, ``docs/ISSUE_AGENT_EXECUTION_CONTRACT.md``
    I7), whose identity must be the exact canonical digest form before
    anything is forwarded; every other path answers 404 and every other
    method on a known path 405;
-   the destination of each route is fixed at startup to the loopback address
    and the internal port of that one authority; no request content (path,
    ``Host``, absolute-form target, headers) can select or alter a destination,
    and only the canonical path is ever sent upstream;
-   the request body is bounded exactly as the edges bound it
    (``MAX_REQUEST_BYTES``), a single strict decimal ``Content-Length`` is
    required, any ``Transfer-Encoding`` is refused before a byte is read, and
    the body is fully read under a finite deadline before anything is
    forwarded;
-   client headers are never forwarded; the upstream request carries only
    ``Content-Type`` and ``Content-Length``;
-   the upstream call has a finite timeout, is made exactly once (the ingress
    never retries, so it cannot duplicate a dispatch), never follows a
    redirect (a 3xx answer is refused as 502), and relays only a bounded,
    ``Content-Length``-framed upstream response;
-   ``/healthz`` is healthy only when both internal authorities answer their
    own health endpoint as the expected service within a short deadline.

The ingress holds no authority. It never parses, verifies or signs the
authorization document, and it is launched with an allowlisted environment
that carries no secret at all -- in particular never the Source Handling
signing key -- so compromising it grants nothing beyond what the public
Internet already has.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import re
import signal
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Final

from issue_agent_edge_transport import (
    MAX_REQUEST_BYTES,
    REQUEST_READ_TIMEOUT_SECONDS,
    BoundedThreadingHTTPServer,
    canonical_json,
    setup_logging,
)

#: Internal authorities are reachable on loopback only; this is not configurable.
LOOPBACK_HOST: Final[str] = "127.0.0.1"

HEALTH_PATH: Final[str] = "/healthz"
PROVISION_PATH: Final[str] = "/issue-agent/provision"
AUTHORIZE_PATH: Final[str] = "/issue-agent/authorize"
#: The read-only execution status route, byte-exact canonical identities only.
STATUS_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"/issue-agent/status/hunter-issue-agent-authorization:[0-9a-f]{64}"
)

#: Health service names answered by the two internal edges.
PROVISIONER_SERVICE_NAME: Final[str] = "hunter-issue-agent-provisioner"
ISSUER_SERVICE_NAME: Final[str] = "hunter-issue-agent-issuer"

SERVICE_NAME: Final[str] = "hunter-issue-agent-ingress"
INGRESS_RESPONSE_SCHEMA_VERSION: Final[str] = "hunter-issue-agent-ingress-response-v1"

#: Finite deadline for one forwarded admission. Both edges acknowledge before
#: any slow provider phase, so this bounds a stalled upstream, not normal work.
#: It stays below the trigger's default dispatch timeout so the trigger sees a
#: deterministic 504 rather than its own socket timeout.
UPSTREAM_TIMEOUT_SECONDS: Final[float] = 300.0

#: Short deadline for each internal health probe.
HEALTH_PROBE_TIMEOUT_SECONDS: Final[float] = 2.0

#: Bound on a relayed upstream response and on a health probe response.
MAX_UPSTREAM_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_HEALTH_RESPONSE_BYTES: Final[int] = 4 * 1024

#: The ingress serves each internal edge's worker pool plus health probes, so
#: it admits twice an edge's bound before answering 503 at the socket.
MAX_CONCURRENT_INGRESS_WORKERS: Final[int] = 16

#: A strict decimal length of at most 19 digits: enough for any bounded body,
#: and short enough that conversion can never hit Python's int-string limit.
_DECIMAL = re.compile(r"[0-9]{1,19}")


@dataclass(frozen=True, slots=True)
class Upstream:
    """One internal authority: fixed loopback port, canonical path, health name."""

    name: str
    port: int
    path: str
    service_name: str


@dataclass(frozen=True, slots=True)
class IngressRoutes:
    """The complete, fixed public route table of the ingress."""

    provisioner: Upstream
    issuer: Upstream

    @classmethod
    def for_ports(cls, *, provisioner_port: int, issuer_port: int) -> IngressRoutes:
        for port in (provisioner_port, issuer_port):
            if not 1 <= port <= 65535:
                raise ValueError("internal upstream ports must be in 1..65535")
        if provisioner_port == issuer_port:
            raise ValueError("provisioner and issuer must listen on distinct internal ports")
        return cls(
            provisioner=Upstream("provisioner", provisioner_port, PROVISION_PATH, PROVISIONER_SERVICE_NAME),
            issuer=Upstream("issuer", issuer_port, AUTHORIZE_PATH, ISSUER_SERVICE_NAME),
        )

    def post_route(self, path: str) -> Upstream | None:
        """Return the upstream for an exact canonical POST path, else ``None``."""
        if path == PROVISION_PATH:
            return self.provisioner
        if path == AUTHORIZE_PATH:
            return self.issuer
        return None

    def upstreams(self) -> tuple[Upstream, Upstream]:
        return (self.provisioner, self.issuer)


class _UpstreamResponseError(Exception):
    """The upstream answered in a shape the ingress refuses to relay."""


def _single_content_length(values: list[str] | None, *, bound: int) -> int | None:
    """Return one strict decimal ``Content-Length`` within *bound*, else ``None``."""
    if not values or len(values) != 1:
        return None
    value = values[0]
    if not _DECIMAL.fullmatch(value):
        return None
    length = int(value)
    return length if length <= bound else None


def probe_upstream(upstream: Upstream, *, timeout: float = HEALTH_PROBE_TIMEOUT_SECONDS) -> bool:
    """Return whether *upstream* answers its own health endpoint as itself."""
    connection = http.client.HTTPConnection(LOOPBACK_HOST, upstream.port, timeout=timeout)
    try:
        connection.request("GET", HEALTH_PATH)
        response = connection.getresponse()
        if response.status != 200:
            return False
        length = _single_content_length(response.headers.get_all("Content-Length"), bound=MAX_HEALTH_RESPONSE_BYTES)
        if length is None:
            return False
        body = response.read(length)
        if len(body) != length:
            return False
        payload = json.loads(body)
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        connection.close()
    return (
        isinstance(payload, dict) and payload.get("status") == "ok" and payload.get("service") == upstream.service_name
    )


class IngressRequestHandler(BaseHTTPRequestHandler):
    """Fixed-route public ingress handler; subclasses bind the route table.

    ``timeout`` is the finite socket read deadline applied before any byte of
    the request body is read.
    """

    routes: IngressRoutes | None = None
    upstream_timeout: float = UPSTREAM_TIMEOUT_SECONDS
    health_timeout: float = HEALTH_PROBE_TIMEOUT_SECONDS
    max_request_bytes: int = MAX_REQUEST_BYTES

    # -- routing ------------------------------------------------------------

    def parse_request(self) -> bool:
        """Parse, then pin ``path`` to the exact request-target the client sent.

        The stdlib collapses a leading ``//`` into ``/``; the ingress routes only
        byte-exact canonical targets, so any normalized target is left
        non-matching and therefore answered 404.  Requests without an explicit
        HTTP version (HTTP/0.9 form) are refused.
        """
        if not super().parse_request():
            return False
        words = self.requestline.split(" ")
        if len(words) != 3:
            self.send_error(400, "Bad request syntax")
            return False
        self.path = words[1]
        return True

    def do_GET(self) -> None:
        if self.path == HEALTH_PATH:
            self._send_health()
        elif STATUS_PATH_RE.fullmatch(self.path) is not None:
            assert self.routes is not None
            self._forward(self.routes.issuer, None, path=self.path)
        elif self._is_known_post_path():
            self._send_error(405, "Method Not Allowed", allow="POST")
        else:
            self._send_error(404, "Not Found")

    def do_POST(self) -> None:
        assert self.routes is not None
        upstream = self.routes.post_route(self.path)
        if upstream is None:
            if self.path == HEALTH_PATH or STATUS_PATH_RE.fullmatch(self.path) is not None:
                self._send_error(405, "Method Not Allowed", allow="GET")
            else:
                self._send_error(404, "Not Found")
            return
        body = self._read_bounded_body()
        if body is None:
            return
        self._forward(upstream, body)

    def _refuse_method(self) -> None:
        if self.path == HEALTH_PATH or STATUS_PATH_RE.fullmatch(self.path) is not None:
            self._send_error(405, "Method Not Allowed", allow="GET")
        elif self._is_known_post_path():
            self._send_error(405, "Method Not Allowed", allow="POST")
        else:
            self._send_error(404, "Not Found")

    do_PUT = _refuse_method
    do_PATCH = _refuse_method
    do_DELETE = _refuse_method
    do_OPTIONS = _refuse_method
    do_TRACE = _refuse_method
    do_CONNECT = _refuse_method

    def do_HEAD(self) -> None:
        # A HEAD response carries no body, so it is answered with headers only.
        known = self.path == HEALTH_PATH or self._is_known_post_path() or STATUS_PATH_RE.fullmatch(self.path)
        self.send_response(405 if known else 404)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def _is_known_post_path(self) -> bool:
        assert self.routes is not None
        return self.routes.post_route(self.path) is not None

    # -- request body -------------------------------------------------------

    def _read_bounded_body(self) -> bytes | None:
        if self.headers.get_all("Transfer-Encoding"):
            self._send_error(501, "Transfer-Encoding is not supported")
            return None
        values = self.headers.get_all("Content-Length")
        if not values:
            self._send_error(411, "Length Required")
            return None
        if len(values) != 1 or not _DECIMAL.fullmatch(values[0]):
            self._send_error(400, "Invalid Content-Length")
            return None
        length = int(values[0])
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
        return body

    # -- forwarding ---------------------------------------------------------

    def _forward(self, upstream: Upstream, body: bytes | None, *, path: str | None = None) -> None:
        """Make exactly one bounded upstream call and relay its framed answer.

        ``body=None`` is the read-only status GET, forwarded to the exact path
        already validated against ``STATUS_PATH_RE``; every other forward is the
        upstream's canonical POST path.
        """
        connection = http.client.HTTPConnection(LOOPBACK_HOST, upstream.port, timeout=self.upstream_timeout)
        try:
            if body is None:
                connection.request("GET", path or upstream.path)
            else:
                connection.request(
                    "POST",
                    upstream.path,
                    body=body,
                    headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
                )
            response = connection.getresponse()
            status = response.status
            if 300 <= status < 400:
                raise _UpstreamResponseError("upstream redirect refused")
            if status < 200 or status > 599:
                raise _UpstreamResponseError("upstream status refused")
            length = _single_content_length(
                response.headers.get_all("Content-Length"), bound=MAX_UPSTREAM_RESPONSE_BYTES
            )
            if length is None or response.headers.get_all("Transfer-Encoding"):
                raise _UpstreamResponseError("upstream response framing refused")
            payload = response.read(length)
            if len(payload) != length:
                raise _UpstreamResponseError("upstream response incomplete")
        except TimeoutError:
            logging.getLogger(__name__).warning("%s upstream timed out", upstream.name)
            self._send_error(504, f"{upstream.name} upstream timed out")
            return
        except _UpstreamResponseError as error:
            logging.getLogger(__name__).warning("%s upstream answer refused: %s", upstream.name, error)
            self._send_error(502, f"{upstream.name} upstream answer refused")
            return
        except (OSError, http.client.HTTPException) as error:
            logging.getLogger(__name__).warning("%s upstream unavailable: %s", upstream.name, type(error).__name__)
            self._send_error(502, f"{upstream.name} upstream unavailable")
            return
        finally:
            connection.close()
        self._send_bytes(status, payload)

    # -- health -------------------------------------------------------------

    def _send_health(self) -> None:
        assert self.routes is not None
        results = {
            upstream.name: "ok" if probe_upstream(upstream, timeout=self.health_timeout) else "unavailable"
            for upstream in self.routes.upstreams()
        }
        healthy = all(state == "ok" for state in results.values())
        payload = {
            "schema_version": INGRESS_RESPONSE_SCHEMA_VERSION,
            "service": SERVICE_NAME,
            "status": "ok" if healthy else "unavailable",
            "upstreams": results,
        }
        self._send_bytes(200 if healthy else 503, canonical_json(payload).encode("utf-8"))

    # -- responses ----------------------------------------------------------

    def _send_bytes(self, status: int, body: bytes, *, allow: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if allow is not None:
            self.send_header("Allow", allow)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str, *, allow: str | None = None) -> None:
        body = canonical_json({"error": message, "schema_version": INGRESS_RESPONSE_SCHEMA_VERSION})
        self._send_bytes(status, body.encode("utf-8"), allow=allow)

    def log_message(self, format: str, *args: Any) -> None:
        # Request line and status only; bodies and headers are never logged.
        logging.getLogger(__name__).info("%s - %s", self.address_string(), format % args)


class IngressServer:
    """Bounded concurrent public ingress over a fixed loopback route table."""

    def __init__(
        self,
        host: str,
        port: int,
        routes: IngressRoutes,
        *,
        read_timeout: float = REQUEST_READ_TIMEOUT_SECONDS,
        upstream_timeout: float = UPSTREAM_TIMEOUT_SECONDS,
        health_timeout: float = HEALTH_PROBE_TIMEOUT_SECONDS,
        max_workers: int = MAX_CONCURRENT_INGRESS_WORKERS,
    ) -> None:
        if port in (routes.provisioner.port, routes.issuer.port):
            raise ValueError("public ingress port must differ from every internal upstream port")
        self._host = host
        self._shutdown_complete = False

        class Handler(IngressRequestHandler):
            timeout = read_timeout

        Handler.routes = routes
        Handler.upstream_timeout = upstream_timeout
        Handler.health_timeout = health_timeout
        self._server = BoundedThreadingHTTPServer((host, port), Handler, max_workers=max_workers)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logging.getLogger(__name__).info("issue-agent ingress listening on %s:%d", self._host, self.port)

    def shutdown(self, timeout: float = 30.0) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=timeout)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hunter_issue_agent_ingress")
    parser.add_argument("--host", default="0.0.0.0", help="public bind address")
    parser.add_argument("--port", type=int, required=True, help="public bind port (Railway $PORT)")
    parser.add_argument("--provisioner-port", type=int, required=True, help="loopback provisioner port")
    parser.add_argument("--issuer-port", type=int, required=True, help="loopback issuer port")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    setup_logging(arguments.verbose)
    logger = logging.getLogger(__name__)
    try:
        routes = IngressRoutes.for_ports(
            provisioner_port=arguments.provisioner_port,
            issuer_port=arguments.issuer_port,
        )
        server = IngressServer(arguments.host, arguments.port, routes)
    except (ValueError, OSError) as error:
        logger.error("ingress configuration error: %s", error)
        return 2

    stopped = threading.Event()

    def _signal_handler(signum: int, frame: Any) -> None:
        logger.info("received signal %d, shutting down", signum)
        stopped.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    try:
        server.start()
        stopped.wait()
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
