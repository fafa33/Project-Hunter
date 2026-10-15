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
    # `gh api` without credentials can return anonymously cached ruleset data.
    # Require an authenticated CLI session before trusting the live contract.
    credential = subprocess.run(("gh", "auth", "token"), capture_output=True, text=True, check=False, timeout=10)
    if credential.returncode or not credential.stdout.strip():
        raise ValueError("Authenticated GitHub ruleset observer required")
    result = subprocess.run(("gh", "api", path), capture_output=True, text=True, check=False, timeout=20)
    if result.returncode:
        raise ValueError("GitHub contract API unavailable or access denied")
    return json.loads(result.stdout)


def observe_contract(repository: str, *, get: Callable[[str], Any] = github_get) -> dict[str, Any]:
    if len(repository.split("/")) != 2 or not all(repository.split("/")):
        raise ValueError("Invalid repository identity")
    rulesets: list[Any] = []
    for page in range(1, 1001):
        batch = get(f"repos/{repository}/rulesets?includes_parents=true&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("Malformed GitHub ruleset list")
        rulesets.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise ValueError("GitHub ruleset pagination exceeded safe bound")
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
                "target": detail.get("target"),
                "conditions": detail.get("conditions"),
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


def _applies_to_ref(ruleset: dict[str, Any], ref: str) -> bool:
    """Fail closed on unknown ruleset conditions; support explicit GitHub ref patterns."""
    from fnmatch import fnmatchcase

    if ruleset.get("target") != "branch":
        return False
    conditions = ruleset.get("conditions")
    if not isinstance(conditions, dict) or set(conditions) != {"ref_name"}:
        raise ValueError("Unsupported ruleset conditions")
    names = conditions["ref_name"]
    if (
        not isinstance(names, dict)
        or not isinstance(names.get("include"), list)
        or not isinstance(names.get("exclude"), list)
    ):
        raise ValueError("Malformed ruleset ref conditions")
    if not all(isinstance(x, str) for x in names["include"] + names["exclude"]):
        raise ValueError("Malformed ruleset ref pattern")

    def matches(pattern: str) -> bool:
        if pattern == "~ALL":
            return True
        if pattern == "~DEFAULT_BRANCH":
            return ref == "refs/heads/main"
        if pattern.startswith("~"):
            raise ValueError("Unsupported GitHub ruleset ref selector")
        normalized = pattern if pattern.startswith("refs/heads/") else f"refs/heads/{pattern}"
        return fnmatchcase(ref, normalized)

    return any(matches(x) for x in names["include"]) and not any(matches(x) for x in names["exclude"])


def required_checks(contract: dict[str, Any], *, target_ref: str | None = None) -> dict[str, int | None]:
    """Extract required contexts and their GitHub App IDs; reject conflicts."""
    result: dict[str, int | None] = {}
    for ruleset in contract["rulesets"]:
        if target_ref is not None and not _applies_to_ref(ruleset, target_ref):
            continue
        for rule in ruleset["rules"]:
            if rule.get("type") != "required_status_checks":
                continue
            entries = (rule.get("parameters") or {}).get("required_status_checks")
            if not isinstance(entries, list):
                raise ValueError("Malformed required checks contract")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("context"), str):
                    raise ValueError("Malformed required check identity")
                name, app_id = entry["context"], entry.get("integration_id")
                if app_id is not None and (type(app_id) is not int or app_id <= 0):
                    raise ValueError("Malformed required check app identity")
                if name in result and result[name] != app_id:
                    raise ValueError("Conflicting required check publishers")
                result[name] = app_id
    return result


def verify_required_checks(
    contract: dict[str, Any],
    runs: list[dict[str, Any]],
    *,
    target_ref: str | None = None,
    statuses: list[dict[str, Any]] | None = None,
    exclude_context: str | None = None,
) -> tuple[bool, str]:
    """Only real successful check-runs from the configured app satisfy a requirement."""
    checks = required_checks(contract, target_ref=target_ref)
    # Excluding the publisher's own context must not turn a self-only
    # contract into an empty, automatically successful verification.
    if exclude_context is not None:
        checks.pop(exclude_context, None)
    if not checks:
        return False, "No independent required checks found in effective contract"
    for name, app_id in checks.items():
        matches = [run for run in runs if run.get("name") == name]
        if not matches:
            status_matches = [item for item in (statuses or []) if item.get("context") == name]
            if not status_matches:
                return False, f"Required check missing: {name}"
            latest_status = max(status_matches, key=lambda item: int(item.get("id") or 0))
            if latest_status.get("state") != "success":
                return False, f"Required commit status not successful: {name}"
            if app_id is not None:
                # Commit status creator.id is a user ID, not a GitHub App ID.
                # Without verifiable App provenance, do not authorize this fallback.
                return False, f"Required status publisher App identity unverifiable: {name}"
            continue
        latest = max(matches, key=lambda run: int(run.get("id") or 0))
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            return False, f"Required check not successful: {name}"
        if app_id is not None and (latest.get("app") or {}).get("id") != app_id:
            return False, f"Required check publisher mismatch: {name}"
    return True, "Required checks and publisher identities verified"
