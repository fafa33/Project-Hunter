from __future__ import annotations

import argparse
import re
from pathlib import Path

_BLACK_PIN = re.compile(r"^black==([0-9]+\.[0-9]+\.[0-9]+)$")
_ALLOWLISTED_BOOTSTRAPS = {"26.3.1": ("pytokens==0.4.0",)}


def read_black_pin(path: Path) -> str:
    """Return the single exact Black pin from a constraints file."""
    pins = [
        match.group(1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := _BLACK_PIN.fullmatch(line.strip()))
    ]
    if len(pins) != 1:
        raise ValueError(f"{path} must contain exactly one exact Black pin")
    return pins[0]


def bootstrap_plan(trusted_pin: str, candidate_pin: str) -> tuple[str, ...]:
    """Return the fully pinned packages required for a safe formatter bootstrap."""
    if candidate_pin == trusted_pin:
        return ()
    dependencies = _ALLOWLISTED_BOOTSTRAPS.get(candidate_pin)
    if dependencies is None:
        raise ValueError(f"Black {candidate_pin} is not an approved trusted-upgrade bootstrap version")
    return (f"black=={candidate_pin}", *dependencies)


def main() -> int:
    """Print a shell-safe, fully pinned bootstrap plan for the trusted workflow."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--trusted-constraints", type=Path, required=True)
    parser.add_argument("--candidate-constraints", type=Path, required=True)
    args = parser.parse_args()

    trusted_pin = read_black_pin(args.trusted_constraints)
    candidate_pin = read_black_pin(args.candidate_constraints)
    for package in bootstrap_plan(trusted_pin, candidate_pin):
        print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
