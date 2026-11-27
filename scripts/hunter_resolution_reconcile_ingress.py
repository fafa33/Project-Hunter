"""Trusted ingress from a completed Issue Agent resolver run to governance reconcile.

A ``workflow_run`` completion is only a wake-up.  This module turns that event
into an identity-only PR set, and fails closed unless the completed run is the
trusted resolver workflow, its resolver job succeeded, and that job published a
well-formed set of PR numbers.  No caller-authored payload, ref selection,
workflow dispatch, or readiness authority is accepted here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

RESOLVER_WORKFLOW = "Hunter / Issue Agent Reconcile"
RESOLVER_JOB = "resolve-finding"
RESOLVE_OUTPUT = "resolved_pr_numbers"
MAX_PULL_REQUEST_NUMBER = 1_000_000
_PR_NUMBER = re.compile(r"^[1-9][0-9]*$")


def select_resolved_pull_requests(
    event: Mapping[str, Any],
    jobs: Mapping[str, Any],
    event_name: str | None = None,
) -> tuple[int, ...]:
    """Return only PRs durably resolved by the trusted resolver job.

    Any ambiguity is an empty result: the scheduled sweep remains recovery, and
    no failed or caller-shaped event can create a false readiness refresh.
    """
    name = event_name if event_name is not None else event.get("name")
    if name != "workflow_run":
        return ()
    run = event.get("workflow_run")
    if not isinstance(run, Mapping) or run.get("name") != RESOLVER_WORKFLOW:
        return ()
    if run.get("conclusion") != "success":
        return ()
    if not _positive_int(run.get("id")) or not _positive_int(run.get("run_attempt")):
        return ()

    entries = jobs.get("jobs")
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        return ()
    resolvers = [item for item in entries if isinstance(item, Mapping) and item.get("name") == RESOLVER_JOB]
    if len(resolvers) != 1 or resolvers[0].get("conclusion") != "success":
        return ()

    values: list[str] = []
    steps = resolvers[0].get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        return ()
    for step in steps:
        if not isinstance(step, Mapping):
            return ()
        outputs = step.get("outputs")
        if outputs is None:
            continue
        if not isinstance(outputs, Mapping):
            return ()
        if RESOLVE_OUTPUT in outputs:
            values.append(outputs[RESOLVE_OUTPUT])
    # Duplicate producer steps are ambiguous provenance, not two resolutions.
    if len(values) != 1 or not isinstance(values[0], str):
        return ()
    return _parse_pr_numbers(values[0])


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _parse_pr_numbers(value: str) -> tuple[int, ...]:
    numbers: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not _PR_NUMBER.fullmatch(token):
            return ()
        number = int(token)
        if number > MAX_PULL_REQUEST_NUMBER:
            return ()
        numbers.add(number)
    return tuple(sorted(numbers)) if numbers else ()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True, type=Path, help="trusted GitHub event payload")
    parser.add_argument("--jobs", required=True, type=Path, help="workflow run jobs from the Actions API")
    parser.add_argument("--event-name", help="GitHub event name when absent from the payload")
    args = parser.parse_args(argv)

    try:
        event = json.loads(args.event.read_text(encoding="utf-8"))
        jobs = json.loads(args.jobs.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"resolution reconcile ingress cannot read trusted evidence: {error}", file=sys.stderr)
        return 2
    if not isinstance(event, Mapping) or not isinstance(jobs, Mapping):
        print("resolution reconcile ingress requires JSON objects", file=sys.stderr)
        return 2

    numbers = select_resolved_pull_requests(event, jobs, args.event_name)
    line = f"{RESOLVE_OUTPUT}={','.join(str(number) for number in numbers)}\n"
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(line)
    sys.stdout.write(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
