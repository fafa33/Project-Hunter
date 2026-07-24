from __future__ import annotations

import sys

from hunter.cli import main as cli_main
from hunter.committee.command import main as committee_authority_main


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "committee-authority":
        return committee_authority_main(arguments[1:])
    return cli_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
