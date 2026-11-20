"""Standalone CLI/consumer for Issue #606 cross-HEAD proof equivalence.

The equivalence authority itself lives in ``hunter_validation_reuse`` (an
already-root-of-trust module), because the trusted reuse decision must never
depend on a module outside the connector write-ingress root of trust. This
script only exposes two operator entry points:

* ``python3 scripts/hunter_ci_final_execution.py --...`` decides cross-HEAD
  proof equivalence for an exact candidate HEAD.
* ``python3 scripts/hunter_ci_final_execution.py measure --...`` collects real
  hosted CI performance evidence from GitHub Actions timestamps.

A successful equivalence never copies an old HEAD's success. It issues a new
current-HEAD-bound decision that references the immutable source proof, and
every missing/ambiguous identity fails closed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from hunter_validation_reuse import (
    _HEX_SHA as _HEX_SHA,
)
from hunter_validation_reuse import (
    PRE_PR_WORKFLOW_PATH as PRE_PR_WORKFLOW_PATH,
)
from hunter_validation_reuse import (
    EquivalenceDecision as EquivalenceDecision,
)
from hunter_validation_reuse import (
    _fetch as _fetch,
)
from hunter_validation_reuse import (
    decide as decide,
)

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RunPerformance:
    """One hosted workflow run's measured, GitHub-timestamped performance record."""

    run_id: int
    run_attempt: int
    head_sha: str
    workflow: str
    status: str
    conclusion: str
    run_started_at: str
    updated_at: str
    workflow_elapsed_seconds: float | None
    expensive_job_name: str
    expensive_job_id: int | None
    job_started_at: str
    job_completed_at: str
    job_elapsed_seconds: float | None
    validation_mode: str
    api_requests: int


def _elapsed(started: str, finished: str) -> float | None:
    """Seconds between two GitHub ISO-8601 timestamps, or None if unavailable."""
    if not started or not finished:
        return None
    try:
        begin = datetime.fromisoformat(started.replace("Z", "+00:00"))
        end = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return None
    if begin.tzinfo is None:
        begin = begin.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    delta = (end - begin).total_seconds()
    return delta if delta >= 0 else None


def collect_performance_evidence(
    repository: str,
    token: str,
    *,
    workflow_path: str = PRE_PR_WORKFLOW_PATH,
    head_shas: frozenset[str] | None = None,
    limit: int = 100,
    fetch: Any = None,
) -> dict[str, Any]:
    """Measure hosted CI cost from real GitHub Actions run/job timestamps.

    Bounded by ``limit`` runs and an explicit per-run job fetch. No synthetic
    durations are produced: a missing or unreadable timestamp is reported as
    ``None``. ``api_requests`` counts the exact GitHub REST calls this
    measurement made; rate-limit headers are not exposed by the shared
    transport and are reported as unavailable rather than guessed.
    """
    requests = 0

    def counted_fetch(path: str) -> Any:
        nonlocal requests
        requests += 1
        if fetch is not None:
            return fetch(path)
        return _fetch(repository, token, path)

    workflow_file = workflow_path.rsplit("/", 1)[-1]
    payload = counted_fetch(
        f"actions/workflows/{quote(workflow_file, safe='')}/runs?per_page={min(max(limit, 1), 100)}"
    )
    records: list[RunPerformance] = []
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        return {
            "repository": repository,
            "workflow_path": workflow_path,
            "api_requests": requests,
            "api_rate_limit_headers": "unavailable (shared transport does not expose headers)",
            "records": [],
            "limitation": "run payload malformed",
        }

    for run in payload["workflow_runs"]:
        if not isinstance(run, dict):
            continue
        if str(run.get("path") or "") != workflow_path:
            continue
        head = str(run.get("head_sha") or "")
        if head_shas is not None and head not in head_shas:
            continue
        run_id = int(run.get("id") or 0)
        attempt = int(run.get("run_attempt") or 1)
        if run_id <= 0:
            continue
        run_started = str(run.get("run_started_at") or run.get("created_at") or "")
        updated = str(run.get("updated_at") or "")
        jobs_payload = counted_fetch(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
        expensive_name = "Hunter Pre-PR Preflight"
        job_id: int | None = None
        job_started = ""
        job_completed = ""
        if isinstance(jobs_payload, dict) and isinstance(jobs_payload.get("jobs"), list):
            for job in jobs_payload["jobs"]:
                if not isinstance(job, dict) or job.get("name") != expensive_name:
                    continue
                job_id = int(job.get("id") or 0) or None
                job_started = str(job.get("started_at") or "")
                job_completed = str(job.get("completed_at") or "")
                break
        records.append(
            RunPerformance(
                run_id=run_id,
                run_attempt=attempt,
                head_sha=head,
                workflow=str(run.get("name") or ""),
                status=str(run.get("status") or ""),
                conclusion=str(run.get("conclusion") or ""),
                run_started_at=run_started,
                updated_at=updated,
                workflow_elapsed_seconds=_elapsed(run_started, updated),
                expensive_job_name=expensive_name,
                expensive_job_id=job_id,
                job_started_at=job_started,
                job_completed_at=job_completed,
                job_elapsed_seconds=_elapsed(job_started, job_completed),
                validation_mode=str(run.get("display_title") or "hosted-full-default"),
                api_requests=requests,
            )
        )
        if len(records) >= max(limit, 1):
            break

    return {
        "repository": repository,
        "workflow_path": workflow_path,
        "api_requests": requests,
        "api_rate_limit_headers": "unavailable (shared transport does not expose headers)",
        "records": [r.__dict__ for r in records],
        "limitations": [
            "Durations derive from GitHub Actions run/job timestamps only; no synthetic values.",
            "validation_mode is a label from the run record; reuse vs fresh is decided by the canonical authority, not inferred here.",
            "required-gate convergence is the max updated_at across relevant checks, measured separately from check-runs evidence.",
        ],
    }


def performance_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure hosted CI performance from GitHub Actions evidence.")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--token", default="")
    parser.add_argument("--workflow-path", default=PRE_PR_WORKFLOW_PATH)
    parser.add_argument("--head-sha", action="append", default=[])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    token = args.token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    report = collect_performance_evidence(
        args.repository,
        token,
        workflow_path=args.workflow_path,
        head_shas=frozenset(args.head_sha) if args.head_sha else None,
        limit=args.limit,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


def _emit(decision: EquivalenceDecision) -> None:
    print(
        f"[Hunter CI Final Execution] {'EQUIVALENT' if decision.equivalent else 'NOT-EQUIVALENT'}: "
        f"{decision.reason}",
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decide cross-HEAD proof equivalence for Issue #606.")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--current-head", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--source-proof-head", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--run-attempt", type=int, default=1)
    parser.add_argument("--job-id", type=int, default=1)
    parser.add_argument("--proof-digest", required=True)
    args = parser.parse_args(argv)

    if not _HEX_SHA.fullmatch(args.current_head):
        print("[Hunter CI Final Execution] FAIL: current-head must be exact 40-hex commit ID", flush=True)
        return 2
    if not _HEX_SHA.fullmatch(args.base_sha):
        print("[Hunter CI Final Execution] FAIL: base-sha must be exact 40-hex commit ID", flush=True)
        return 2
    if args.source_proof_head and not _HEX_SHA.fullmatch(args.source_proof_head):
        print("[Hunter CI Final Execution] FAIL: source-proof-head must be exact 40-hex commit ID", flush=True)
        return 2
    if args.pr <= 0:
        print("[Hunter CI Final Execution] FAIL: PR number must be positive", flush=True)
        return 2
    if args.run_attempt <= 0:
        print("[Hunter CI Final Execution] FAIL: run-attempt must be positive", flush=True)
        return 2
    if args.job_id <= 0:
        print("[Hunter CI Final Execution] FAIL: job-id must be positive", flush=True)
        return 2
    if not args.proof_digest:
        print("[Hunter CI Final Execution] FAIL: proof-digest is required", flush=True)
        return 2

    decision = decide(
        ROOT,
        repository=args.repository,
        pr_number=args.pr,
        current_head=args.current_head,
        base_sha=args.base_sha,
        source_proof_head=args.source_proof_head or None,
        token=args.token,
        run_attempt=args.run_attempt,
        job_id=args.job_id,
        proof_digest=args.proof_digest,
    )
    _emit(decision)
    return 0 if decision.equivalent else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "measure":
        raise SystemExit(performance_main(sys.argv[2:]))
    raise SystemExit(main())
