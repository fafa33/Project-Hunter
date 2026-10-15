"""HGCC-1: read-only GitHub contract observation for the existing agent push boundary.

No check-run, commit status, workflow, or merge authority is created here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any


def github_get(path: str) -> Any:
    result = subprocess.run(("gh", "api", path), capture_output=True, text=True, check=False, timeout=20)
    if result.returncode:
        raise ValueError("GitHub contract API unavailable or access denied")
    return json.loads(result.stdout)


def observe_contract(repository: str, *, get: Callable[[str], Any] = github_get) -> dict[str, Any]:
    if len(repository.split("/")) != 2 or not all(repository.split("/")):
        raise ValueError("Invalid repository identity")
    rulesets = get(f"repos/{repository}/rulesets?includes_parents=true")
    if not isinstance(rulesets, list):
        raise ValueError("Malformed GitHub ruleset list")
    active = [item for item in rulesets if isinstance(item, dict) and item.get("enforcement") == "active"]
    if not active:
        raise ValueError("No active GitHub rulesets observed")
    observations = []
    for item in active:
        ruleset_id = item.get("id")
        if not isinstance(ruleset_id, int):
            raise ValueError("Malformed GitHub ruleset identity")
        detail = get(f"repos/{repository}/rulesets/{ruleset_id}")
        if not isinstance(detail, dict) or not isinstance(detail.get("rules"), list):
            raise ValueError("Malformed GitHub ruleset detail")
        observations.append(
            {
                "id": ruleset_id,
                "name": detail.get("name"),
                "rules": detail["rules"],
            }
        )
    return {"schema": "hgcc-1/v1", "repository": repository, "rulesets": observations}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: hunter_github_contract.py OWNER/REPO", file=sys.stderr)
        return 2
    try:
        contract = observe_contract(sys.argv[1])
    except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"[HGCC-1] INDETERMINATE: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(contract, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
