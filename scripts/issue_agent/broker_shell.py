"""Task shell entrypoint: sends command to trusted broker, never runs host shell."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from hunter.automation.issue_agent_shell_broker import run_client


def main() -> int:
    socket_path = os.environ.get("HUNTER_SHELL_BROKER_SOCKET")
    if not socket_path or len(sys.argv) < 2:
        print("DENIED: missing governed broker or command", file=sys.stderr)
        return 126
    # The tool calls a shell with '-c command'; never interpret it locally.
    if len(sys.argv) == 3 and sys.argv[1] == "-c":
        command = sys.argv[2]
    else:
        print("DENIED: unsupported shell invocation", file=sys.stderr)
        return 126
    try:
        return run_client(Path(socket_path), command)
    except (OSError, ValueError, KeyError):
        print("DENIED: governed broker unavailable", file=sys.stderr)
        return 126


if __name__ == "__main__":
    sys.exit(main())
