"""Single public ingress for the governed Issue-agent Railway service.

These tests drive the real ingress server over real sockets against stub
loopback upstreams that record every request they receive, so each routing,
bounding and fail-closed property is proven at the wire rather than on a router
function.  The end-to-end topology with the real provisioner and issuer edges
lives in ``tests/test_issue_agent_provisioner.py``.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from typing import Any

import hunter_issue_agent_ingress as ingress
import pytest
from issue_agent_edge_transport import MAX_REQUEST_BYTES, BoundedThreadingHTTPServer

DOCUMENT = b'{"authorization":{},"issuer_signature":"00"}'


class _Upstream:
    """A loopback stub edge that records requests and answers a scripted reply."""

    def __init__(self, service_name: str) -> None:
        self.service_name = service_name
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.body = b'{"status":"accepted"}'
        self.extra_headers: dict[str, str] = {}
        self.delay = 0.0
        self.chunked = False
        self.health_service = service_name
        self.lock = threading.Lock()
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = json.dumps({"service": upstream.health_service, "status": "ok"}).encode()
                time.sleep(upstream.delay)
                self.send_response(200 if self.path == "/healthz" else 404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                with upstream.lock:
                    upstream.requests.append(
                        {"path": self.path, "headers": dict(self.headers.items()), "body": self.rfile.read(length)}
                    )
                time.sleep(upstream.delay)
                self.send_response(upstream.status)
                for name, value in upstream.extra_headers.items():
                    self.send_header(name, value)
                if upstream.chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(upstream.body), upstream.body))
                    return
                self.send_header("Content-Length", str(len(upstream.body)))
                self.end_headers()
                self.wfile.write(upstream.body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler, max_workers=8)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _Topology:
    def __init__(self, **server_kwargs: Any) -> None:
        self.provisioner = _Upstream(ingress.PROVISIONER_SERVICE_NAME)
        self.issuer = _Upstream(ingress.ISSUER_SERVICE_NAME)
        routes = ingress.IngressRoutes.for_ports(provisioner_port=self.provisioner.port, issuer_port=self.issuer.port)
        self.server = ingress.IngressServer("127.0.0.1", 0, routes, **server_kwargs)
        self.server.start()
        self.port = self.server.port

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            connection.putrequest(method, path, skip_host=False, skip_accept_encoding=True)
            for name, value in (headers or {}).items():
                connection.putheader(name, value)
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, dict(response.headers.items()), response.read()
        finally:
            connection.close()

    def post(self, path: str, body: bytes = DOCUMENT, **headers: str) -> tuple[int, dict[str, str], bytes]:
        merged = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
        merged.update({name.replace("_", "-"): value for name, value in headers.items()})
        return self.request("POST", path, body, merged)

    def raw(self, payload: bytes, *, timeout: float = 15) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as connection:
            connection.sendall(payload)
            chunks = []
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks)

    def upstream_hits(self) -> int:
        return len(self.provisioner.requests) + len(self.issuer.requests)

    def close(self) -> None:
        self.server.shutdown()
        self.provisioner.close()
        self.issuer.close()


@pytest.fixture
def topology() -> Iterator[Any]:
    built: list[_Topology] = []

    def _make(**kwargs: Any) -> _Topology:
        instance = _Topology(**kwargs)
        built.append(instance)
        return instance

    yield _make
    for instance in built:
        instance.close()


def _status_line(raw: bytes) -> int:
    return int(raw.split(b" ", 2)[1])


# --- Routing ----------------------------------------------------------------


def test_provision_routes_only_to_the_provisioner(topology: Any) -> None:
    edges = topology()
    status, _, body = edges.post("/issue-agent/provision")

    assert status == 200
    assert body == b'{"status":"accepted"}'
    assert [request["path"] for request in edges.provisioner.requests] == ["/issue-agent/provision"]
    assert edges.provisioner.requests[0]["body"] == DOCUMENT
    assert edges.issuer.requests == []


def test_authorize_routes_only_to_the_issuer(topology: Any) -> None:
    edges = topology()
    status, _, _ = edges.post("/issue-agent/authorize")

    assert status == 200
    assert [request["path"] for request in edges.issuer.requests] == ["/issue-agent/authorize"]
    assert edges.issuer.requests[0]["body"] == DOCUMENT
    assert edges.provisioner.requests == []


def test_upstream_status_and_body_are_relayed_byte_for_byte(topology: Any) -> None:
    edges = topology()
    edges.issuer.status = 409
    edges.issuer.body = b'{"error":"replay","schema_version":"x"}'

    status, headers, body = edges.post("/issue-agent/authorize")

    assert status == 409
    assert body == b'{"error":"replay","schema_version":"x"}'
    assert headers["Content-Type"] == "application/json"


def test_client_headers_are_never_forwarded(topology: Any) -> None:
    edges = topology()
    edges.post(
        "/issue-agent/provision",
        Authorization="Bearer secret",
        X_Forwarded_Host="evil.example",
        Cookie="a=b",
    )

    forwarded = {name.lower() for name in edges.provisioner.requests[0]["headers"]}
    assert forwarded <= {"host", "accept-encoding", "content-type", "content-length"}
    assert edges.provisioner.requests[0]["headers"]["Host"] == f"127.0.0.1:{edges.provisioner.port}"


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/issue-agent",
        "/issue-agent/provision/",
        "/issue-agent/provision?x=1",
        "/issue-agent/authorize#frag",
        "/Issue-Agent/Provision",
        "//issue-agent/provision",
        "/issue-agent/provision/../authorize",
        "/issue-agent/%70rovision",
        "http://evil.example/issue-agent/provision",
        "http://127.0.0.1:1/issue-agent/authorize",
    ],
)
def test_unknown_paths_fail_closed_without_any_upstream_contact(topology: Any, path: str) -> None:
    edges = topology()
    status, _, body = edges.post(path)

    assert status == 404
    assert json.loads(body)["error"] == "Not Found"
    assert edges.upstream_hits() == 0


def test_host_header_cannot_select_a_destination(topology: Any) -> None:
    edges = topology()
    status, _, _ = edges.post("/issue-agent/authorize", Host="evil.example:80")

    assert status == 200
    assert len(edges.issuer.requests) == 1


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/issue-agent/provision", 405),
        ("GET", "/issue-agent/authorize", 405),
        ("POST", "/healthz", 405),
        ("PUT", "/issue-agent/provision", 405),
        ("DELETE", "/issue-agent/authorize", 405),
        ("PATCH", "/issue-agent/provision", 405),
        ("OPTIONS", "/issue-agent/provision", 405),
        ("TRACE", "/issue-agent/provision", 405),
        ("PUT", "/elsewhere", 404),
        ("GET", "/elsewhere", 404),
        ("PROPFIND", "/issue-agent/provision", 501),
    ],
)
def test_unsupported_methods_fail_closed(topology: Any, method: str, path: str, expected: int) -> None:
    edges = topology()
    status, _, _ = edges.request(method, path, DOCUMENT, {"Content-Length": str(len(DOCUMENT))})

    assert status == expected
    assert edges.upstream_hits() == 0


def test_http09_request_without_version_is_refused(topology: Any) -> None:
    edges = topology()
    raw = edges.raw(b"GET //healthz\r\n\r\n")

    assert b"Bad request syntax" in raw
    assert ingress.SERVICE_NAME.encode() not in raw


def test_connect_method_is_not_a_tunnel(topology: Any) -> None:
    edges = topology()
    raw = edges.raw(b"CONNECT 127.0.0.1:%d HTTP/1.1\r\nHost: x\r\n\r\n" % edges.issuer.port)

    assert _status_line(raw) == 404
    assert edges.upstream_hits() == 0


# --- Request body bounds ------------------------------------------------------


def test_oversized_body_is_refused_before_reading_or_forwarding(topology: Any) -> None:
    edges = topology()
    raw = edges.raw(
        b"POST /issue-agent/provision HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n" % (MAX_REQUEST_BYTES + 1)
    )

    assert _status_line(raw) == 413
    assert edges.upstream_hits() == 0


def test_body_at_the_bound_is_forwarded(topology: Any) -> None:
    edges = topology()
    body = b"x" * MAX_REQUEST_BYTES
    status, _, _ = edges.post("/issue-agent/provision", body)

    assert status == 200
    assert edges.provisioner.requests[0]["body"] == body


@pytest.mark.parametrize("value", ["-1", "-0", "+5", "5 ", "0x10", "1e3", "", "5,5"])
def test_malformed_or_negative_content_length_fails_closed(topology: Any, value: str) -> None:
    edges = topology()
    raw = edges.raw(b"POST /issue-agent/authorize HTTP/1.1\r\nHost: x\r\nContent-Length: %s\r\n\r\n" % value.encode())

    assert _status_line(raw) == 400
    assert edges.upstream_hits() == 0


def test_duplicate_content_length_fails_closed(topology: Any) -> None:
    edges = topology()
    raw = edges.raw(
        b"POST /issue-agent/authorize HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}"
    )

    assert _status_line(raw) == 400
    assert edges.upstream_hits() == 0


def test_missing_content_length_fails_closed(topology: Any) -> None:
    edges = topology()
    raw = edges.raw(b"POST /issue-agent/authorize HTTP/1.1\r\nHost: x\r\n\r\n")

    assert _status_line(raw) == 411
    assert edges.upstream_hits() == 0


@pytest.mark.parametrize("encoding", ["chunked", "gzip, chunked", "identity"])
def test_transfer_encoding_never_opens_an_unbounded_body_path(topology: Any, encoding: str) -> None:
    edges = topology()
    raw = edges.raw(
        b"POST /issue-agent/provision HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: %s\r\nContent-Length: 2\r\n\r\n"
        b"2\r\n{}\r\n0\r\n\r\n" % encoding.encode()
    )

    assert _status_line(raw) == 501
    assert edges.upstream_hits() == 0


def test_withheld_body_times_out_without_forwarding(topology: Any) -> None:
    edges = topology(read_timeout=0.3)
    started = time.monotonic()
    raw = edges.raw(b"POST /issue-agent/provision HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\n{}")

    assert _status_line(raw) == 408
    assert time.monotonic() - started < 5
    assert edges.upstream_hits() == 0


# --- Upstream failure ---------------------------------------------------------


def test_unavailable_upstream_fails_closed_with_502(topology: Any) -> None:
    edges = topology()
    edges.issuer.close()

    status, _, body = edges.post("/issue-agent/authorize")

    assert status == 502
    assert "issuer" in json.loads(body)["error"]


def test_stalled_upstream_times_out_with_504(topology: Any) -> None:
    edges = topology(upstream_timeout=0.3)
    edges.provisioner.delay = 2.0
    started = time.monotonic()

    status, _, _ = edges.post("/issue-agent/provision")

    assert status == 504
    assert time.monotonic() - started < 2.0


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_upstream_redirect_is_never_followed_or_relayed(topology: Any, code: int) -> None:
    edges = topology()
    edges.issuer.status = code
    edges.issuer.extra_headers = {"Location": f"http://127.0.0.1:{edges.provisioner.port}/issue-agent/provision"}

    status, headers, _ = edges.post("/issue-agent/authorize")

    assert status == 502
    assert "Location" not in headers
    assert len(edges.issuer.requests) == 1
    assert edges.provisioner.requests == []


def test_oversized_upstream_response_is_refused(topology: Any) -> None:
    edges = topology()
    edges.provisioner.body = b"x" * (ingress.MAX_UPSTREAM_RESPONSE_BYTES + 1)

    status, _, _ = edges.post("/issue-agent/provision")

    assert status == 502


def test_unframed_upstream_response_is_refused(topology: Any) -> None:
    edges = topology()
    edges.provisioner.chunked = True

    status, _, _ = edges.post("/issue-agent/provision")

    assert status == 502


# --- No duplicate dispatch -----------------------------------------------------


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_ingress_never_retries_an_upstream_dispatch(topology: Any, code: int) -> None:
    edges = topology()
    edges.issuer.status = code

    status, _, _ = edges.post("/issue-agent/authorize")

    assert status == code
    assert len(edges.issuer.requests) == 1


def test_timed_out_dispatch_is_not_retried(topology: Any) -> None:
    edges = topology(upstream_timeout=0.3)
    edges.issuer.delay = 1.0

    status, _, _ = edges.post("/issue-agent/authorize")
    time.sleep(1.2)

    assert status == 504
    assert len(edges.issuer.requests) == 1


# --- Health ----------------------------------------------------------------------


def test_health_is_ok_only_when_both_internal_authorities_answer(topology: Any) -> None:
    edges = topology()
    status, _, body = edges.request("GET", "/healthz")

    assert status == 200
    assert json.loads(body) == {
        "schema_version": ingress.INGRESS_RESPONSE_SCHEMA_VERSION,
        "service": ingress.SERVICE_NAME,
        "status": "ok",
        "upstreams": {"issuer": "ok", "provisioner": "ok"},
    }


@pytest.mark.parametrize("dead", ["provisioner", "issuer"])
def test_health_fails_when_a_required_internal_service_is_unavailable(topology: Any, dead: str) -> None:
    edges = topology()
    getattr(edges, dead).close()

    status, _, body = edges.request("GET", "/healthz")

    assert status == 503
    payload = json.loads(body)
    assert payload["status"] == "unavailable"
    assert payload["upstreams"][dead] == "unavailable"


def test_health_fails_when_a_port_answers_as_the_wrong_service(topology: Any) -> None:
    edges = topology()
    edges.provisioner.health_service = ingress.ISSUER_SERVICE_NAME

    status, _, _ = edges.request("GET", "/healthz")

    assert status == 503


def test_health_probe_of_a_stalled_service_is_bounded(topology: Any) -> None:
    edges = topology(health_timeout=0.3)
    edges.issuer.delay = 3.0
    started = time.monotonic()

    status, _, _ = edges.request("GET", "/healthz")

    assert status == 503
    assert time.monotonic() - started < 2.5


# --- Configuration ------------------------------------------------------------------


def test_internal_ports_must_be_distinct_and_valid() -> None:
    with pytest.raises(ValueError):
        ingress.IngressRoutes.for_ports(provisioner_port=8081, issuer_port=8081)
    with pytest.raises(ValueError):
        ingress.IngressRoutes.for_ports(provisioner_port=0, issuer_port=8082)
    with pytest.raises(ValueError):
        ingress.IngressRoutes.for_ports(provisioner_port=8081, issuer_port=65536)


def test_public_port_cannot_collide_with_an_internal_port() -> None:
    routes = ingress.IngressRoutes.for_ports(provisioner_port=_free_port(), issuer_port=_free_port() + 1)
    with pytest.raises(ValueError):
        ingress.IngressServer("127.0.0.1", routes.provisioner.port, routes)
    with pytest.raises(ValueError):
        ingress.IngressServer("127.0.0.1", routes.issuer.port, routes)


def test_upstream_host_is_fixed_to_loopback() -> None:
    assert ingress.LOOPBACK_HOST == "127.0.0.1"
    assert "--upstream-host" not in ingress._parser().format_help()


def test_canonical_endpoint_contracts_are_unchanged() -> None:
    import hunter_issue_agent_issuer as issuer
    import hunter_issue_agent_provisioner as provisioner

    assert ingress.PROVISION_PATH == provisioner._ProvisionerRequestHandler.endpoint == "/issue-agent/provision"
    assert ingress.AUTHORIZE_PATH == issuer._IssuerRequestHandler.endpoint == "/issue-agent/authorize"
    assert ingress.PROVISIONER_SERVICE_NAME == provisioner._ProvisionerRequestHandler.service_name
    assert ingress.ISSUER_SERVICE_NAME == issuer._IssuerRequestHandler.service_name
    assert ingress.MAX_REQUEST_BYTES == MAX_REQUEST_BYTES
