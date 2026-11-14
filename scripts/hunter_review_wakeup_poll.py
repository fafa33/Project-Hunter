"""Bounded read-only review-change detector for the existing trusted readiness workflow.

This process never publishes readiness or evaluates merge authority. Its sole
mutation is dispatching the default-branch controller with a PR number.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY = "fafa33/Project-Hunter"
MAX_ACTIVE_PRS = 5
INTERVAL_SECONDS = 8
MAX_DISPATCHES_PER_HOUR = 60
STATE_PATH = Path(os.environ.get("HUNTER_REVIEW_WAKEUP_STATE", str(Path.home() / ".hunter-review-wakeup-state.json")))


def gh(*args: str) -> Any:
    result = subprocess.run(("gh", *args), check=True, text=True, capture_output=True, timeout=12)
    return json.loads(result.stdout)


def active_prs() -> tuple[int, ...]:
    data = gh("api", f"repos/{REPOSITORY}/pulls?state=open&base=main&per_page={MAX_ACTIVE_PRS + 1}")
    if not isinstance(data, list) or len(data) > MAX_ACTIVE_PRS:
        raise ValueError("active PR budget exceeded; no partial polling")
    return tuple(sorted(int(pr["number"]) for pr in data))


def review_revision(number: int) -> str:
    # The live GraphQL review-thread nodes capture new bot/human findings and
    # resolution transitions. Reviews capture CHANGES_REQUESTED transitions.
    query = """query($owner:String!,$repo:String!,$number:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$number){headRefOid reviewThreads(first:100){pageInfo{hasNextPage} nodes{id isResolved comments(first:100){pageInfo{hasNextPage} nodes{id body updatedAt}}}} reviews(first:100){pageInfo{hasNextPage} nodes{id state submittedAt}}}}}"""
    data = gh(
        "api",
        "graphql",
        "-f",
        f"query={query}",
        "-F",
        "owner=fafa33",
        "-F",
        "repo=Project-Hunter",
        "-F",
        f"number={number}",
    )
    pr = ((data.get("data") or {}).get("repository") or {}).get("pullRequest")
    if not isinstance(pr, dict):
        raise ValueError("review evidence unavailable")
    for collection in ("reviewThreads", "reviews"):
        block = pr.get(collection) or {}
        if (block.get("pageInfo") or {}).get("hasNextPage"):
            raise ValueError("review evidence pagination limit exceeded")
        if collection == "reviewThreads":
            for thread in block.get("nodes") or []:
                if ((thread.get("comments") or {}).get("pageInfo") or {}).get("hasNextPage"):
                    raise ValueError("review comments pagination limit exceeded")
    return hashlib.sha256(json.dumps(pr, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def dispatch(number: int) -> None:
    subprocess.run(
        (
            "gh",
            "workflow",
            "run",
            "hunter-merge-readiness.yml",
            "--repo",
            REPOSITORY,
            "--ref",
            "main",
            "-f",
            f"pr_number={number}",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=12,
    )


def load_state(path: Path = STATE_PATH) -> tuple[dict[int, str], list[float]]:
    try:
        payload = json.loads(path.read_text())
        if payload.get("version") != 1:
            raise ValueError("unsupported state version")
        previous = {int(k): str(v) for k, v in payload["revisions"].items()}
        dispatched = [float(t) for t in payload["dispatches"]]
        return previous, dispatched
    except FileNotFoundError:
        return {}, []
    except (ValueError, KeyError, TypeError) as exc:
        # Never silently replace an unknown checkpoint with a new baseline.
        raise RuntimeError(f"wakeup checkpoint unavailable: {exc}") from exc


def save_state(previous: dict[int, str], dispatched: list[float], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temp.open("x") as stream:
            json.dump({"version": 1, "revisions": previous, "dispatches": dispatched}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def loop() -> None:
    previous, dispatched = load_state(STATE_PATH)
    while True:
        started = time.monotonic()
        try:
            numbers = active_prs()
            current: dict[int, str] = {}
            for number in numbers:
                try:
                    revision = review_revision(number)
                    if number in previous and previous[number] != revision:
                        dispatched = [t for t in dispatched if 0 <= time.time() - t < 3600]
                        if len(dispatched) >= MAX_DISPATCHES_PER_HOUR:
                            raise RuntimeError("dispatch budget exhausted")
                        # Reserve the hourly budget durably before the external side effect.
                        # An uncertain dispatch outcome is never replayed automatically.
                        reserved = dispatched + [time.time()]
                        checkpoint = {**previous, number: revision}
                        save_state(checkpoint, reserved, STATE_PATH)
                        previous, dispatched = checkpoint, reserved
                        dispatch(number)
                        print(
                            json.dumps(
                                {
                                    "event": "review_changed",
                                    "pr": number,
                                    "dispatch_seconds": round(time.monotonic() - started, 3),
                                    "observed_at": datetime.now(UTC).isoformat(),
                                }
                            ),
                            flush=True,
                        )
                    current[number] = previous.get(number, revision)
                except OSError:
                    raise
                except Exception as exc:
                    # Do not mark an unknown revision as baseline; retry next cycle.
                    current.pop(number, None)
                    print(f"PR #{number}: {type(exc).__name__}: {exc}", flush=True)
            previous = {**{n: previous[n] for n in numbers if n in previous and n not in current}, **current}
            save_state(previous, dispatched, STATE_PATH)
        except OSError:
            # Persistence is an authority prerequisite: stop rather than dispatch
            # further changes with an unaccounted budget. LaunchAgent retries.
            raise
        except Exception as exc:
            print(f"poll unavailable: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(max(0, INTERVAL_SECONDS - (time.monotonic() - started)))


if __name__ == "__main__":
    loop()
