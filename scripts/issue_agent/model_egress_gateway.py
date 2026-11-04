"""Fail-closed HTTPS CONNECT proxy for an internally networked model container.

Deploy only with the agent on an INTERNAL Docker network. The gateway alone
may attach to an egress network; the model container must have no other network.
"""

from __future__ import annotations

import ipaddress
import os
import select
import socket
import socketserver

MAX_HEADER = 4096
ALLOWED_HOSTS = frozenset({"api.kilo.ai", "api.kilocode.ai"})


def permitted_target(request: bytes) -> tuple[str, int] | None:
    if len(request) > MAX_HEADER or b"\r\n\r\n" not in request:
        return None
    try:
        header = request.split(b"\r\n", 1)[0].decode("ascii")
        method, target, version = header.split(" ")
        if method != "CONNECT" or version != "HTTP/1.1" or target.count(":") != 1:
            return None
        host, port = target.split(":")
        if host not in ALLOWED_HOSTS or port != "443":
            return None
        if any(ord(ch) < 33 or ord(ch) > 126 for ch in host):
            return None
        return host, 443
    except (UnicodeError, ValueError):
        return None


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
    def handle(self) -> None:
        self.request.settimeout(8)
        header = b""
        try:
            while b"\r\n\r\n" not in header and len(header) <= MAX_HEADER:
                part = self.request.recv(min(1024, MAX_HEADER + 1 - len(header)))
                if not part:
                    break
                header += part
            target = permitted_target(header)
            if target is None:
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
    with socketserver.ThreadingTCPServer(("0.0.0.0", 8080), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
