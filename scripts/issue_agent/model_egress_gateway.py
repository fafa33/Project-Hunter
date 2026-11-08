"""Fail-closed HTTPS CONNECT proxy for an internally networked model container.

Deploy only with the agent on an INTERNAL Docker network. The gateway alone
may attach to an egress network; the model container must have no other network.
"""

from __future__ import annotations

import ipaddress
import os
import re
import select
import socket
import socketserver

MAX_HEADER = 4096
#: The pinned Kilo provider host. ADR 0041 F8: the ``kilo-canary`` profile is the Kilo profile without it.
KILO_PROVIDER_HOST = "ai-gateway.kilo.ai"
ALLOWED_HOSTS = frozenset({KILO_PROVIDER_HOST, "models.dev"})
#: Issue #577. Kilo 7.8.18's offline guard (``KiloSessionProcessor.offlineGuard``) probes connectivity whenever a
#: model stream is silent for 10 s -- a reasoning model thinking before its first token. Its probe is a raw TCP dial
#: to the provider (impossible from the internal network) and then ``HEAD https://kilo.ai`` (then example.com and
#: cloudflare.com) through this proxy. With every probe refused, Kilo aborts the healthy stream as
#: ``DisconnectedError`` and the headless run ends ``APIError "Network connection failed"`` (hosted runs 42486218343
#: and 42487085826). Admitting the vendor's own apex for its liveness probe keeps the stream alive; no third party is
#: admitted. The canary profile deliberately omits it, so a simulated provider outage still reads as offline.
KILO_LIVENESS_HOST = "kilo.ai"
#: ADR 0041 F5: the macOS Codex fallback gateway. ChatGPT-plan Codex signs in at auth.openai.com and runs against
#: chatgpt.com. It listens on loopback only; the sandbox profile admits no other network destination.
CODEX_ALLOWED_HOSTS = frozenset({"auth.openai.com", "chatgpt.com"})
CODEX_LISTEN = ("127.0.0.1", 18443)
PROFILES = {
    "kilo": (ALLOWED_HOSTS | {KILO_LIVENESS_HOST}, ("0.0.0.0", 8080)),  # noqa: S104
    # ADR 0041 F8: armed only by the signed AUTHORIZED.fallback_canary flag, for one primary run's gateway instance.
    "kilo-canary": (ALLOWED_HOSTS - {KILO_PROVIDER_HOST}, ("0.0.0.0", 8080)),  # noqa: S104
    "codex": (CODEX_ALLOWED_HOSTS, CODEX_LISTEN),
}
#: One content-free line per well-formed CONNECT refused only because its host is outside the profile.
REFUSED_HOST_MARKER = "GATEWAY_REFUSED_HOST"
_HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")


def connect_target(request: bytes) -> tuple[str, int] | None:
    """The ``host:443`` of one well-formed HTTP/1.1 ``CONNECT`` request, before any allowlist decision."""

    if len(request) > MAX_HEADER or b"\r\n\r\n" not in request:
        return None
    try:
        header = request.split(b"\r\n", 1)[0].decode("ascii")
        method, target, version = header.split(" ")
        if method != "CONNECT" or version != "HTTP/1.1" or target.count(":") != 1:
            return None
        host, port = target.split(":")
        if port != "443" or _HOSTNAME.fullmatch(host) is None:
            return None
        return host, 443
    except (UnicodeError, ValueError):
        return None


def permitted_target(request: bytes, allowed: frozenset[str] = ALLOWED_HOSTS) -> tuple[str, int] | None:
    target = connect_target(request)
    return target if target is not None and target[0] in allowed else None


def public_addresses(host: str) -> list[tuple]:
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    result = []
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise PermissionError("non-public resolved destination")
        result.append(address)
    if not result:
        raise PermissionError("no public destination")
    return result


class Handler(socketserver.BaseRequestHandler):
    allowed: frozenset[str] = ALLOWED_HOSTS

    def handle(self) -> None:
        self.request.settimeout(8)
        header = b""
        try:
            while b"\r\n\r\n" not in header and len(header) <= MAX_HEADER:
                part = self.request.recv(min(1024, MAX_HEADER + 1 - len(header)))
                if not part:
                    break
                header += part
            requested = connect_target(header)
            target = requested if requested is not None and requested[0] in self.allowed else None
            if requested is not None and target is None:
                print(REFUSED_HOST_MARKER, requested[0], flush=True)
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            if target is None:
                print("GATEWAY_DENIED_CONNECT", repr(header.split(b"\r\n", 1)[0][:180]), flush=True)
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            for family, kind, proto, _, address in public_addresses(target[0]):
                try:
                    with socket.socket(family, kind, proto) as upstream:
                        upstream.settimeout(8)
                        upstream.connect(address)
                        self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                        self.request.settimeout(None)
                        upstream.settimeout(None)
                        while True:
                            readable, _, _ = select.select([self.request, upstream], [], [], 60)
                            if not readable:
                                return
                            for source in readable:
                                data = source.recv(16384)
                                if not data:
                                    return
                                (upstream if source is self.request else self.request).sendall(data)
                        return
                except (OSError, TimeoutError):
                    continue
            self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        except (OSError, TimeoutError, PermissionError):
            return


def main() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("gateway must not run as root")
    profile = os.environ.get("HUNTER_GATEWAY_PROFILE", "kilo")
    if profile not in PROFILES:
        raise RuntimeError("unknown gateway profile")
    allowed, listen = PROFILES[profile]
    handler = type("ProfileHandler", (Handler,), {"allowed": allowed})
    with socketserver.ThreadingTCPServer(listen, handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
