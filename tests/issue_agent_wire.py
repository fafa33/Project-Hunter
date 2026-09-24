"""Shared Issue #497 composition-test HTTP wire client.

Both edge composition suites (``tests/test_issue_agent_issuer.py`` and
``tests/test_issue_agent_provisioner.py``) drive a real threaded HTTPServer on
an ephemeral port and read plaintext HTTP responses.  The ``GET`` client and
the server ``shutdown`` teardown are byte-for-byte identical in both files, so
they live here instead of drifting apart.
"""

from __future__ import annotations

import http.client
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from http.server import HTTPServer


class EdgeTransportClientMixin:
    """HTTP wire client for an edge HTTPServer started on an ephemeral port.

    Concrete edge classes set ``port`` and ``server`` before using the mixin.
    """

    port: int
    server: HTTPServer

    def get(self, path: str) -> tuple[int, str]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        connection.request("GET", path)
        response = connection.getresponse()
        received = response.read()
        connection.close()
        return response.status, received.decode("utf-8")

    def close(self) -> None:
        self.server.shutdown()


def issue_body_with_scope(body: str) -> str:
    """Attach the canonical owner scope fixture used by Issue-agent tests."""
    scope = {
        "branch_pattern": "issue-*",
        "base_ref": "main",
        "base_sha": "a" * 40,
        "allowed_paths": ["src/", "scripts/", "tests/", "docs/"],
        "prohibited_paths": [],
    }
    return body + "\n<!-- hunter-task-scope-v1\n" + json.dumps(scope, separators=(",", ":")) + "\n-->"
