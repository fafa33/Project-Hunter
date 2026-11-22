"""Start the private no-subscription Workspace Authority transport.

Expose only through an independently authenticated private tunnel; never
forward the writer credential to an agent or public workflow.
"""

from __future__ import annotations

import os
from pathlib import Path

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch
from hunter.automation.workspace_authority_http import WorkspaceAuthorityHTTPServer
from hunter.automation.workspace_sqlite_backend import WorkspaceSQLiteBackend


def main() -> None:
    path = os.environ.get("HUNTER_WORKSPACE_SQLITE_PATH", "")
    key_hex = os.environ.get("HUNTER_WORKSPACE_AUTHORITY_KEY_HEX", "")
    reader = os.environ.get("HUNTER_WORKSPACE_READER_TOKEN", "")
    writer = os.environ.get("HUNTER_WORKSPACE_WRITER_TOKEN", "")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError:
        raise WorkspaceAuthorityMismatch("invalid authority key") from None
    backend = WorkspaceSQLiteBackend(Path(path), authority_key=key)
    server = WorkspaceAuthorityHTTPServer(
        (
            "127.0.0.1",
            (
                0
                if os.environ.get("HUNTER_WORKSPACE_PORT") == "0"
                else int(os.environ.get("HUNTER_WORKSPACE_PORT", "8765"))
            ),
        ),
        backend=backend,
        reader_token=reader,
        writer_token=writer,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
