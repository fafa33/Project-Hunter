"""Bind the Issue #682 acceptance proof to GitHub's authoritative job identity (trusted step, no agent input).

Reads the proof record emitted by the signed Kilo E2E, then re-derives repository, PR, head SHA, run, attempt and the
numeric job id from the GitHub Actions API using the runner token. The agent runs in a separate container and can never
write this process's inputs; mismatches, duplicates, stale attempts or missing facts all fail closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

PROOF_PREFIX = "HUNTER_682_ACCEPTANCE_PROOF "
CANONICAL_PREFIX = "HUNTER_682_CANONICAL_PROOF "
_SHA40 = re.compile(r"[0-9a-f]{40}")


class ProofRejected(Exception):
    pass


def extract_proof(log: str) -> dict[str, Any]:
    lines = [line.split(PROOF_PREFIX, 1)[1] for line in log.splitlines() if PROOF_PREFIX in line]
    if len(lines) != 1:
        raise ProofRejected(f"expected exactly one acceptance proof record, found {len(lines)}")
    proof = json.loads(lines[0])
    claimed = proof.pop("proof_sha256", None)
    if hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest() != claimed:
        raise ProofRejected("proof digest does not match its content")
    proof["proof_sha256"] = claimed
    return proof


def canonicalize(
    proof: Mapping[str, Any],
    *,
    run: Mapping[str, Any],
    jobs: Mapping[str, Any],
    event: Mapping[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    """Pure verification: returns the canonical record or raises ``ProofRejected``."""

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise ProofRejected(reason)

    pull = event.get("pull_request") or {}
    head = (pull.get("head") or {}).get("sha")
    require(isinstance(head, str) and _SHA40.fullmatch(head) is not None, "event carries no exact PR head")
    require(proof.get("head_sha") == head == run.get("head_sha"), "head SHA differs between proof, event and run")
    require(
        proof.get("repository") == env.get("GITHUB_REPOSITORY") == (run.get("repository") or {}).get("full_name"),
        "repository mismatch",
    )
    require(str(proof.get("run_id")) == str(run.get("id")) == env.get("GITHUB_RUN_ID"), "run id mismatch")
    require(
        str(proof.get("run_attempt")) == str(run.get("run_attempt")) == env.get("GITHUB_RUN_ATTEMPT"),
        "stale or replayed attempt",
    )
    require(proof.get("pull_request") == pull.get("number"), "pull request mismatch")
    require(
        proof.get("pull_request") in {p.get("number") for p in run.get("pull_requests", [])},
        "run is not bound to the PR",
    )
    require(proof.get("validator_verdict") == "PASS", "validator did not pass")
    matching = [job for job in jobs.get("jobs", []) if job.get("name") == proof.get("job")]
    require(len(matching) == 1, "job identity is not unique")
    job = matching[0]
    require(isinstance(job.get("id"), int) and job["id"] > 0, "job has no numeric id")
    require(
        str(job.get("run_id")) == str(run.get("id")) and str(job.get("run_attempt")) == str(run.get("run_attempt")),
        "job belongs to another run/attempt",
    )
    require(job.get("head_sha") == head, "job head SHA mismatch")
    require(job.get("status") == "in_progress", "job is not the currently executing job")
    record = {
        **{k: v for k, v in proof.items() if k != "proof_sha256"},
        "job_id": job["id"],
        "job_url": job.get("html_url"),
    }
    record["acceptance_proof_sha256"] = proof["proof_sha256"]
    record["canonical_proof_sha256"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    return record


def _get(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https GitHub API origin
        return json.load(response)


def main(stdin: TextIO, fetch: Callable[[str, str], dict[str, Any]] = _get) -> int:
    """The log arrives on stdin (``< "$RUNNER_TEMP/..."``): no file path is ever taken from the command line."""
    env = os.environ
    repository, run_id, attempt = env["GITHUB_REPOSITORY"], env["GITHUB_RUN_ID"], env["GITHUB_RUN_ATTEMPT"]
    api = f"https://api.github.com/repos/{repository}/actions/runs/{run_id}"
    try:
        proof = extract_proof(stdin.read())
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
        record = canonicalize(
            proof,
            run=fetch(f"{api}/attempts/{attempt}", env["GITHUB_TOKEN"]),
            jobs=fetch(f"{api}/attempts/{attempt}/jobs?per_page=100", env["GITHUB_TOKEN"]),
            event=event,
            env=env,
        )
    except (ProofRejected, KeyError, ValueError, OSError) as error:
        print(f"CANONICAL PROOF REJECTED: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(CANONICAL_PREFIX + json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin))
