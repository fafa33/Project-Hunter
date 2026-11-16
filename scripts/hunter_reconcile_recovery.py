"""Bounded writer-side recovery for a failed post-push Governance Reconcile.

Issue #585. When an agent pushes a remediation and the committed pre-ready
review request no longer describes the new exact head, the trusted
default-branch reconcile reports a blocked exact-head review prerequisite
(``WAITING_FOR_EXACT_HEAD_REVIEW``) and the pull request waits. This module is
the bounded recovery step for exactly that state.

Trust contract
--------------
This module is recovery support for the existing trusted lifecycle, not a
parallel one. It observes live GitHub evidence read-only: it never dispatches a
workflow, never reruns CI, never publishes a status, never marks a pull request
Ready, and never merges. Readiness remains the exclusive authority of the
trusted controllers; nothing here re-establishes any proof another stage owns.

The only candidate mutation it can perform is regenerating the committed
pre-ready review request through the existing authorized-writer finalizer
(``hunter_pre_ready_review.finalize_writer_review_request``). A review request
carries no review authority, so re-minting it for the current content fabricates
nothing; the regenerated artifact reaches GitHub only through the writer's
normal signed commit/push boundary.

Classification, binding and bounds
----------------------------------
- ``stale-request``: the authoritative reconcile job reported a blocked
  exact-head review prerequisite for this exact pull-request head. Recoverable:
  regenerate the content-bound request through the existing finalizer, then
  commit and push through the signed writer boundary.
- ``transient``: the reconcile failed on GitHub evidence unavailability. The
  existing scheduled and event-driven reconcile sweeps retry it; no recovery
  attempt is spent and no CI is rerun.
- ``permanent``: any other failure, or failure evidence that cannot be bound to
  the exact head. It stays blocked and is reported with a specific diagnosis;
  failed recovery terminates instead of pending indefinitely.

Every observed event is recorded in a durable local JSON checkpoint keyed by
(pull request, exact head, failure kind). A repeated event for the same key is
idempotent and never creates a second attempt; attempt counters survive the
bounded history trim so the per-head budget can never reset itself; the
stale-HEAD check refuses recovery bound to content that no longer exists; and
the per-head attempt budget is bounded like the hosted preflight-recovery
budget.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hunter_github_transport as transport
import hunter_pre_ready_review as review

REPOSITORY = "fafa33/Project-Hunter"
RECONCILE_WORKFLOW_FILE = ".github/workflows/hunter-governance-reconcile.yml"
AUTHORITATIVE_JOB_NAME = "Reconcile open pull requests"
MAX_SCANNED_RUNS = 5
#: Mirrors the hosted recovery budget so a writer-side loop can never exceed it.
MAX_RECOVERY_ATTEMPTS = 2
MAX_TRACKED_EVENTS = 16
STATE_SCHEMA = "hunter.reconcile-recovery-state.v1"
#: The sanctioned checkpoint directories. The recovery checkpoint path arrives
#: from the CLI or the environment — exactly the input a faulty agent argument
#: can get wrong — so before any file operation it is user-expanded, resolved,
#: and required to stay inside one of these roots. A faulty argument can then
#: never read or replace a file outside the recovery bookkeeping area.
STATE_ROOTS = (
    (Path(__file__).resolve().parents[1] / ".hunter").resolve(strict=False),
    (Path.home() / ".hunter").resolve(strict=False),
)
STATE_FILENAME = "reconcile-recovery-state.json"
STATE_PATH = Path(os.environ.get("HUNTER_RECONCILE_RECOVERY_STATE", str(Path.home() / ".hunter" / STATE_FILENAME)))
#: Log markers that classify an authoritative governance-refresh failure as
#: infrastructure unavailability rather than a candidate defect.
TRANSIENT_MARKERS = ("infrastructure unavailable", "rate limit", "GitHubUnavailable")
#: How far back from a refresh-failure marker the invoking pull request's own
#: stderr can reach: the bridge prints its transient diagnosis and exits, and
#: the workflow then echoes the failure marker for that same pull request, so
#: the invocation's transient evidence sits in the lines immediately before
#: that marker. A transient marker anywhere else in the shared job log belongs
#: to another pull request's invocation and must never reclassify this failure.
TRANSIENT_CONTEXT_LINES = 10
STALE_REQUEST_MARKER = "WAITING_FOR_EXACT_HEAD_REVIEW"
REFRESH_FAILURE_MARKER = "Governance refresh failed"
ORCHESTRATION_FAILURE_MARKER = "Review orchestration failed"
#: One reconcile job log is a bounded read; a larger body is unknown, not evidence.
MAX_LOG_BYTES = 8 * 1024 * 1024

KIND_STALE_REQUEST = "stale-request"
KIND_TRANSIENT = "transient"
KIND_PERMANENT = "permanent"


class ObservationUnavailable(RuntimeError):
    """Trusted reconcile evidence is unreadable; recovery must fail closed."""


@dataclass(frozen=True)
class ReconcileFailure:
    """One observed reconcile failure, bound to its exact run, job and head."""

    kind: str  # stale-request | transient | permanent
    run_id: int
    job_id: int
    head_sha: str
    detail: str


@dataclass(frozen=True)
class RecoveryPlan:
    """The bounded, actionable repair step returned to the responsible writer."""

    pr_number: int
    head_sha: str
    attempt: int
    run_id: int
    job_id: int
    action: str


@dataclass(frozen=True)
class RecoveryRefusal:
    """Why recovery did not happen; a specific diagnosis, never a silent skip."""

    code: str  # STALE_HEAD | ALREADY_RECOVERED | RECOVERY_BUDGET_EXHAUSTED | TRANSIENT | PERMANENT
    reason: str


def request_json(repository: str, token: str, method: str, path: str) -> Any:
    """The shared bounded-retry GitHub REST transport; no other request path exists."""

    return transport.request_rest_json(
        url=f"https://api.github.com/repos/{repository}/{path}",
        method=method,
        headers={},
        data=None,
        token=token,
        what=f"{method} {path}",
    )


def default_job_log(repository: str, token: str) -> Callable[[int], str]:
    """One Actions job log as bounded text; the signed blob URL never receives the token."""

    def get(url: str, *, auth: bool, opener: urllib.request.OpenerDirector) -> bytes:
        headers = {"Accept": "application/vnd.github+json"}
        if auth:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310 - fixed https API endpoint
        with opener.open(request, timeout=60) as response:
            return response.read()

    def read(job_id: int) -> str:
        if type(job_id) is not int or job_id < 1:
            raise ValueError("a job log is read by its exact positive job id")

        # The logs endpoint answers 302 with a signed blob URL. urllib forwards
        # the Authorization header to the redirect target, and Azure rejects
        # foreign credentials, so the redirect is followed by hand, unauthenticated.
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            body = get(
                f"https://api.github.com/repos/{repository}/actions/jobs/{job_id}/logs", auth=True, opener=opener
            )
        except urllib.error.HTTPError as redirect:
            if redirect.status not in (302, 307):
                raise
            location = redirect.headers.get("Location", "")
            if urllib.parse.urlsplit(location).scheme != "https":
                raise ObservationUnavailable("the job log redirect target is not https") from None
            body = get(location, auth=False, opener=opener)
        if len(body) > MAX_LOG_BYTES:
            raise ObservationUnavailable("the authoritative job log exceeds the bounded read size")
        return body.decode("utf-8", errors="replace")

    return read


def _pr_lines(text: str, pr_number: int) -> list[str]:
    return [line for line in text.splitlines() if re.search(rf"PR #{pr_number}(?![0-9])", line)]


def observe_failure(
    repository: str,
    token: str,
    pr_number: int,
    head_sha: str,
    *,
    request: Callable[[str, str, str, str], Any] | None = None,
    read_log: Callable[[int], str],
) -> ReconcileFailure | None:
    """The exact failed reconcile run and authoritative job for this exact head, or ``None``.

    The first scanned run that carries evidence for this pull request decides; a
    run whose authoritative job is missing or ambiguous is not evidence, and an
    unreadable log fails closed. A run whose authoritative log never mentions
    this pull request did not reconcile it at all -- its silence is not evidence
    of success -- so the scan continues into the older bounded runs; only a run
    that reconciled this pull request without failure evidence ends the scan.
    """

    request = request or request_json
    workflows = request(repository, token, "GET", "actions/workflows?per_page=100")
    listing = workflows.get("workflows") if isinstance(workflows, dict) else None
    if not isinstance(listing, list):
        raise ObservationUnavailable("reconcile workflow listing is unreadable")
    named = [item for item in listing if isinstance(item, dict) and item.get("path") == RECONCILE_WORKFLOW_FILE]
    if len(named) != 1:
        raise ObservationUnavailable("the reconcile workflow is not exactly one workflow")
    runs = request(repository, token, "GET", f"actions/workflows/{named[0]['id']}/runs?per_page={MAX_SCANNED_RUNS}")
    run_list = runs.get("workflow_runs") if isinstance(runs, dict) else None
    if not isinstance(run_list, list):
        raise ObservationUnavailable("reconcile run listing is unreadable")
    for run in run_list[:MAX_SCANNED_RUNS]:
        if not isinstance(run, dict) or type(run.get("id")) is not int:
            continue
        jobs = request(repository, token, "GET", f"actions/runs/{run['id']}/jobs?per_page=100")
        job_list = jobs.get("jobs") if isinstance(jobs, dict) else None
        if not isinstance(job_list, list):
            raise ObservationUnavailable(f"run {run['id']} jobs are unreadable")
        authoritative = [job for job in job_list if isinstance(job, dict) and job.get("name") == AUTHORITATIVE_JOB_NAME]
        if len(authoritative) != 1 or type(authoritative[0].get("id")) is not int:
            # A job listing without exactly the one trusted reconcile job is not
            # evidence about this head; fail closed instead of classifying.
            raise ObservationUnavailable(f"run {run['id']} has no single authoritative reconcile job")
        job = authoritative[0]
        text = read_log(int(job["id"]))
        if not isinstance(text, str) or not text.strip():
            raise ObservationUnavailable(f"run {run['id']} job log is unreadable")
        lines = _pr_lines(text, pr_number)
        stale = [line for line in lines if STALE_REQUEST_MARKER in line and head_sha[:10] in line]
        refresh_failed = [line for line in lines if REFRESH_FAILURE_MARKER in line]
        orchestration_failed = [line for line in lines if ORCHESTRATION_FAILURE_MARKER in line]
        if orchestration_failed:
            return ReconcileFailure(
                KIND_PERMANENT, int(run["id"]), int(job["id"]), head_sha, orchestration_failed[0].strip()
            )
        if stale:
            return ReconcileFailure(KIND_STALE_REQUEST, int(run["id"]), int(job["id"]), head_sha, stale[0].strip())
        if refresh_failed:
            if str(run.get("conclusion")) != "failure":
                continue
            # Transient evidence is only attributed to this invocation when it
            # sits in the bounded lines immediately before this pull request's
            # own failure marker; the job log reconciles many pull requests, so
            # a transient marker anywhere else belongs to another pull request
            # and must never reclassify this failure.
            marker_index = text.splitlines().index(refresh_failed[0])
            invocation = text.splitlines()[max(0, marker_index - TRANSIENT_CONTEXT_LINES) : marker_index + 1]
            transient = any(marker in line for line in invocation for marker in TRANSIENT_MARKERS)
            return ReconcileFailure(
                KIND_TRANSIENT if transient else KIND_PERMANENT,
                int(run["id"]),
                int(job["id"]),
                head_sha,
                refresh_failed[0].strip(),
            )
        if not lines:
            # This run never reconciled this pull request; keep searching the
            # older bounded runs instead of mistaking its silence for success.
            continue
        # This run reconciled our pull request with no failure evidence for it.
        return None
    return None


def validate_state_path(path: str | Path) -> Path:
    """Bind an externally supplied checkpoint path to the sanctioned roots.

    The checkpoint path is CLI- or environment-supplied, i.e. exactly the input
    a faulty agent argument can get wrong. It is user-expanded and resolved,
    must name a ``.json`` checkpoint, and must stay inside one of the sanctioned
    checkpoint directories; anything else raises and recovery fails closed
    instead of reading or replacing a file outside the bookkeeping area.
    """

    resolved = Path(path).expanduser().resolve(strict=False)
    if resolved.suffix != ".json":
        raise ObservationUnavailable(f"the recovery checkpoint must be a .json file, not {resolved}")
    for root in STATE_ROOTS:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    raise ObservationUnavailable(
        f"the recovery checkpoint path escapes the sanctioned checkpoint directories: {resolved}"
    )


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    path = validate_state_path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": STATE_SCHEMA, "events": []}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ObservationUnavailable(f"recovery checkpoint is unreadable ({exc})") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema") != STATE_SCHEMA
        or not isinstance(document.get("events"), list)
    ):
        raise ObservationUnavailable("recovery checkpoint has an unsupported schema")
    return document


def save_state(document: dict[str, Any], path: Path = STATE_PATH) -> None:
    """Atomic, bounded checkpoint: an uncertain write is never treated as recorded.

    Trimming the tracked history may never drop an attempt counter: the budget
    in ``plan_recovery`` is counted from this durable checkpoint, so a trimmed
    counter would silently reset the bounded budget and unbound recovery. Only
    events without an attempt counter are trimmed to the tracked-event bound,
    the trim is stable, and re-saving the saved state changes nothing.
    """

    path = validate_state_path(path)
    recorded = [item for item in document["events"] if isinstance(item, dict)]
    counters = [item for item in recorded if item.get("attempt") is not None]
    budget = max(MAX_TRACKED_EVENTS - len(counters), 0)
    events = counters + [item for item in recorded if item.get("attempt") is None][-budget:]
    payload = {"schema": STATE_SCHEMA, "events": events}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temp.open("x") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def record_event(document: dict[str, Any], event: dict[str, Any], path: Path = STATE_PATH) -> None:
    document["events"].append(event)
    save_state(document, path)


def plan_recovery(
    failure: ReconcileFailure,
    document: dict[str, Any],
    *,
    pr_number: int,
    head_sha: str,
) -> RecoveryPlan | RecoveryRefusal:
    """The durable decision for one failure: a bounded plan or a specific refusal."""

    if failure.kind == KIND_TRANSIENT:
        return RecoveryRefusal(
            "TRANSIENT",
            "the reconcile failed on GitHub evidence unavailability; the existing scheduled and "
            "event-driven reconcile sweeps retry it, no recovery attempt is spent",
        )
    if failure.kind == KIND_PERMANENT:
        return RecoveryRefusal(
            "PERMANENT", failure.detail or "unclassified reconcile failure; recovery is not authorized"
        )
    if failure.head_sha[:10] != head_sha[:10]:
        return RecoveryRefusal("STALE_HEAD", f"the failure describes head {failure.head_sha[:10]}, not {head_sha[:10]}")
    events = [item for item in document["events"] if isinstance(item, dict)]

    def head_matches(item: Any) -> bool:
        return (
            item.get("pr_number") == pr_number
            and str(item.get("head_sha", "")).lower() == head_sha.lower()
            and item.get("kind") == failure.kind
        )

    # Idempotence is per failure event: the same authoritative run observed
    # again never creates a second attempt. A NEW failing run for the same head
    # may spend the next bounded budget attempt.
    if any(
        head_matches(item) and item.get("run_id") == failure.run_id and item.get("attempt") is not None
        for item in events
    ):
        return RecoveryRefusal(
            "ALREADY_RECOVERED",
            "this exact failure event already produced a recovery attempt; repeating it would create "
            "a push/CI rerun loop",
        )
    attempts = sum(1 for item in events if head_matches(item) and item.get("attempt") is not None)
    if attempts >= MAX_RECOVERY_ATTEMPTS:
        return RecoveryRefusal(
            "RECOVERY_BUDGET_EXHAUSTED",
            f"the bounded recovery budget ({MAX_RECOVERY_ATTEMPTS} attempt(s)) is spent for this head",
        )
    return RecoveryPlan(
        pr_number=pr_number,
        head_sha=head_sha,
        attempt=attempts + 1,
        run_id=failure.run_id,
        job_id=failure.job_id,
        action=(
            "regenerate the exact-head review request from a freshly substantiated judgement with "
            "`python scripts/hunter_pre_ready_review.py --refresh-request <judgement.json> --issue <N> "
            "--base <merge-base>` (or let the pre-commit finalizer refresh it), then commit and push "
            "through the signed writer boundary; the trusted reconcile re-verifies the new head"
        ),
    )


def execute_recovery(
    plan: RecoveryPlan,
    *,
    root: Path,
    issue: str,
    judgement: dict[str, Any],
) -> dict[str, Any]:
    """Regenerate the content-bound request through the existing authorized-writer finalizer.

    The writer remains responsible for signing, committing and pushing the
    regenerated artifact; nothing here publishes anything.
    """

    head = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=root, text=True).strip()
    if head.lower() != plan.head_sha.lower():
        raise ObservationUnavailable(
            f"local HEAD {head[:10]} is not the recovery-bound head {plan.head_sha[:10]}; refusing to bind"
        )
    base = subprocess.check_output(("git", "merge-base", "HEAD", "origin/main"), cwd=root, text=True).strip()
    changed = review.finalize_writer_review_request(
        issue=issue, base=base, head=head, base_ref="main", judgement=judgement, cwd=root
    )
    return {
        "schema": "hunter.reconcile-recovery-attempt.v1",
        "pr_number": plan.pr_number,
        "head_sha": plan.head_sha,
        "attempt": plan.attempt,
        "run_id": plan.run_id,
        "job_id": plan.job_id,
        "regenerated": changed,
        "next_step": (
            "stage and commit the regenerated request with HUNTER_REVIEW_JUDGEMENT and HUNTER_GOVERNING_ISSUE "
            "pointing at this same freshly substantiated judgement (the pre-commit finalizer re-verifies it "
            "against the exact staged head and fails closed without it), then push through "
            "`python3 scripts/hunter_push.py`"
            if changed
            else "the request already describes this exact head; wait for the next trusted reconcile sweep"
        ),
    }


def _output(document: dict[str, Any]) -> None:
    print(json.dumps(document, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded recovery for a failed post-push Governance Reconcile")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True, help="Exact PR head SHA recovery must bind to")
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--state", default=None, help=f"Recovery checkpoint path (default: {STATE_PATH})")
    parser.add_argument("--execute", action="store_true", help="Run the recovery, not just plan it")
    parser.add_argument("--issue", help="Governing Issue number (required with --execute)")
    parser.add_argument(
        "--judgement", dest="judgement", help="Freshly substantiated judgement JSON (required with --execute)"
    )
    arguments = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        state_path = validate_state_path(arguments.state or STATE_PATH)
        pr = request_json(arguments.repository, token, "GET", f"pulls/{arguments.pr}")
        live_head = str((pr.get("head") or {}).get("sha") or "") if isinstance(pr, dict) else ""
        if live_head.lower() != arguments.head.lower():
            refusal = RecoveryRefusal(
                "STALE_HEAD",
                f"live PR head {live_head[:10] or '(unavailable)'} is not the requested recovery head "
                f"{arguments.head[:10]}",
            )
            _output({"refused": refusal.code, "reason": refusal.reason})
            return 2
        failure = observe_failure(
            arguments.repository,
            token,
            arguments.pr,
            arguments.head,
            read_log=default_job_log(arguments.repository, token),
        )
        document = load_state(state_path)
        if failure is None:
            _output({"observed": "none", "head_sha": arguments.head, "pr_number": arguments.pr})
            return 0
        plan = plan_recovery(failure, document, pr_number=arguments.pr, head_sha=arguments.head)
        if isinstance(plan, RecoveryRefusal):
            record_event(
                document,
                {
                    "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "pr_number": arguments.pr,
                    "head_sha": arguments.head,
                    "kind": failure.kind,
                    "run_id": failure.run_id,
                    "job_id": failure.job_id,
                    "refused": plan.code,
                    "reason": plan.reason,
                },
                state_path,
            )
            _output({"refused": plan.code, "reason": plan.reason, "kind": failure.kind})
            return 2
        if not arguments.execute:
            _output(
                {
                    "planned": True,
                    "kind": failure.kind,
                    "attempt": plan.attempt,
                    "run_id": plan.run_id,
                    "job_id": plan.job_id,
                    "head_sha": plan.head_sha,
                    "action": plan.action,
                }
            )
            return 0
        if not arguments.issue or not arguments.judgement:
            print("--execute requires --issue and --judgement; recovery cannot fabricate a judgement", file=sys.stderr)
            return 2
        judgement = json.loads(review._trusted_judgement_path(arguments.judgement).read_text(encoding="utf-8"))
        if not isinstance(judgement, dict):
            raise ValueError("review request judgement must be a JSON object")
        attempt = execute_recovery(
            plan, root=Path(__file__).resolve().parents[1], issue=arguments.issue, judgement=judgement
        )
        record_event(
            document,
            {
                "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "pr_number": arguments.pr,
                "head_sha": arguments.head,
                "kind": failure.kind,
                "run_id": failure.run_id,
                "job_id": failure.job_id,
                "attempt": plan.attempt,
                "regenerated": attempt["regenerated"],
            },
            state_path,
        )
        _output(attempt)
        return 0
    except (
        ObservationUnavailable,
        transport.GitHubRequestError,
        subprocess.CalledProcessError,
        urllib.error.URLError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"recovery unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
