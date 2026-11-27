"""Trusted ingress from a completed Issue Agent resolver run to governance reconcile.

A ``workflow_run`` completion is only a wake-up.  This module turns that event
into an identity-only PR set, and fails closed unless the completed run is the
trusted resolver workflow, its resolver job succeeded, and that exact job
published a uniquely named, digest-bound Actions artifact containing verified
PR identities.  GitHub's jobs API has no ``steps[].outputs`` field, so step
prose is never evidence here.  No caller-authored payload, ref selection,
workflow dispatch, or readiness authority is accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
import re
import sys
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

RESOLVER_WORKFLOW = "Hunter / Issue Agent Reconcile"
RESOLVER_JOB = "resolve-finding"
RESOLVE_OUTPUT = "resolved_pr_numbers"
EVIDENCE_SCHEMA = "hunter-resolution-reconcile-evidence-v1"
EVIDENCE_FILE = "resolution-evidence.json"
MAX_PULL_REQUEST_NUMBER = 1_000_000
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
_PR_NUMBER = re.compile(r"^[1-9][0-9]*$")


def select_resolved_pull_requests(
    event: Mapping[str, Any],
    jobs: Mapping[str, Any],
    *,
    artifacts: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    archive: bytes,
    event_name: str | None = None,
    repository: str,
) -> tuple[int, ...]:
    """Return only PRs durably resolved by the trusted resolver job.

    Every identity is bound to one authenticated workflow_run, one successful
    resolver job, one unexpired artifact with a verified digest, and one signed
    evidence document.  Any ambiguity is an empty result: the scheduled sweep
    remains recovery, and no failed or caller-shaped event can create a false
    readiness refresh.
    """
    name = event_name if event_name is not None else event.get("name")
    if name != "workflow_run":
        return ()
    run = event.get("workflow_run")
    event_repository = event.get("repository")
    if not isinstance(run, Mapping) or not isinstance(event_repository, Mapping):
        return ()
    if event_repository.get("full_name") != repository or not _positive_int(event_repository.get("id")):
        return ()
    if run.get("name") != RESOLVER_WORKFLOW or run.get("conclusion") != "success":
        return ()
    if not _positive_int(run.get("id")) or not _positive_int(run.get("run_attempt")):
        return ()
    head = run.get("head_sha")
    if not isinstance(head, str) or _SHA40.fullmatch(head) is None:
        return ()
    run_id, attempt = int(run["id"]), int(run["run_attempt"])
    repository_id = int(event_repository["id"])
    if run.get("status") != "completed":
        return ()

    # The documented jobs response has status/conclusion/name fields but no
    # step outputs. A resolver identity comes only from the unique successful job.
    entries = jobs.get("jobs")
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        return ()
    resolvers = [item for item in entries if isinstance(item, Mapping) and item.get("name") == RESOLVER_JOB]
    if len(resolvers) != 1:
        return ()
    job = resolvers[0]
    if (
        job.get("conclusion") != "success"
        or job.get("status") != "completed"
        or job.get("run_id") != run_id
        or job.get("run_attempt") != attempt
        or job.get("head_sha") != head
        or job.get("workflow_name") != RESOLVER_WORKFLOW
    ):
        return ()
    steps = job.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)) or not steps:
        return ()
    for step in steps:
        if not isinstance(step, Mapping) or step.get("status") != "completed" or step.get("conclusion") != "success":
            return ()

    expected_name = f"hunter-resolution-reconcile-{run_id}-{attempt}"
    listed = artifacts.get("artifacts")
    if not isinstance(listed, Sequence) or isinstance(listed, (str, bytes)):
        return ()
    matches = [item for item in listed if isinstance(item, Mapping) and item.get("name") == expected_name]
    if len(matches) != 1:
        return ()
    artifact = matches[0]
    artifact_run = artifact.get("workflow_run")
    if not isinstance(artifact_run, Mapping) or bool(artifact.get("expired")) or not _positive_int(artifact.get("id")):
        return ()
    digest = artifact.get("digest")
    if not isinstance(digest, str) or (match := _DIGEST.fullmatch(digest)) is None:
        return ()
    if not hmac.compare_digest(match.group(1), hashlib.sha256(archive).hexdigest()):
        return ()
    if (
        artifact_run.get("id") != run_id
        or artifact_run.get("repository_id") != repository_id
        or artifact_run.get("head_repository_id") != repository_id
        or artifact_run.get("head_sha") != head
    ):
        return ()

    parsed = _evidence_document(archive)
    if parsed is None or (evidence is not None and parsed != dict(evidence)):
        return ()
    if (
        parsed.get("schema") != EVIDENCE_SCHEMA
        or parsed.get("repository") != repository
        or parsed.get("workflow") != RESOLVER_WORKFLOW
        or parsed.get("run_id") != run_id
        or parsed.get("run_attempt") != attempt
        or parsed.get("head_sha") != head
    ):
        return ()
    return _parse_pr_numbers(parsed.get("pr_numbers"))


def _evidence_document(archive: bytes) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = [item for item in bundle.infolist() if not item.is_dir()]
            if len(members) != 1 or members[0].filename != EVIDENCE_FILE:
                return None
            document = json.loads(bundle.read(EVIDENCE_FILE).decode("utf-8"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None
    return document if isinstance(document, dict) else None


def _parse_pr_numbers(value: object) -> tuple[int, ...]:
    if isinstance(value, str):
        tokens: Sequence[object] = re.split(r"[\n,]+", value)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)):
        tokens = value
    else:
        return ()
    numbers: set[int] = set()
    for token in tokens:
        if not isinstance(token, int) or isinstance(token, bool) or not _PR_NUMBER.fullmatch(str(token)):
            return ()
        number = int(token)
        if number > MAX_PULL_REQUEST_NUMBER:
            return ()
        numbers.add(number)
    return tuple(sorted(numbers))


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _write_output(numbers: tuple[int, ...]) -> str:
    """One shell-readable block: no prefix to consume, one PR identity per line."""

    return "".join(f"{number}\n" for number in numbers)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True, type=Path, help="trusted GitHub event payload")
    parser.add_argument("--jobs", required=True, type=Path, help="workflow run jobs from the Actions API")
    parser.add_argument("--artifacts", required=True, type=Path, help="workflow run artifacts from the Actions API")
    parser.add_argument("--evidence-zip", required=True, type=Path, help="authenticated artifact download")
    parser.add_argument("--repository", required=True, help="expected trusted repository full name")
    parser.add_argument("--event-name", help="GitHub event name when absent from the payload")
    parser.add_argument("--for-shell", action="store_true", help="emit bare PR numbers for a shell while-read loop")
    args = parser.parse_args(argv)

    try:
        event = json.loads(args.event.read_text(encoding="utf-8"))
        jobs = json.loads(args.jobs.read_text(encoding="utf-8"))
        artifacts = json.loads(args.artifacts.read_text(encoding="utf-8"))
        archive = args.evidence_zip.read_bytes()
    except (OSError, ValueError) as error:
        print(f"resolution reconcile ingress cannot read trusted evidence: {error}", file=sys.stderr)
        return 2
    if not all(isinstance(item, Mapping) for item in (event, jobs, artifacts)):
        print("resolution reconcile ingress requires JSON objects", file=sys.stderr)
        return 2

    numbers = select_resolved_pull_requests(
        event,
        jobs,
        artifacts=artifacts,
        evidence=None,
        archive=archive,
        event_name=args.event_name,
        repository=args.repository,
    )
    block = _write_output(numbers)
    if args.for_shell:
        sys.stdout.write(block)
        return 0

    # A multi-line GITHUB_OUTPUT value requires a unique delimiter; a bare
    # `key=value` line would only carry the first PR number.
    delimiter = "HUNTER_RESOLUTION_PR_NUMBERS"
    line = f"{RESOLVE_OUTPUT}<<{delimiter}\n{block}{delimiter}\n"
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(line)
    sys.stdout.write(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
