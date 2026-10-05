"""Whether pull-request CI may reuse the trusted hosted exact-head proof.

Issue #415: ``Quality Gates`` re-ran the identical full repository suite on the
identical candidate content simply because a pull request existed. Where the
integration tree GitHub produced is byte-for-byte the tree the trusted branch
preflight already validated, that second run establishes nothing the first did
not, and it is the single largest avoidable delay between a final code change
and a reviewable pull request.

Reuse is authorized by two things this job cannot forge and one it recomputes:

1. **Tree identity.** ``git`` content addressing decides whether the merge tree
   and the validated candidate tree are the same content. They diverge as soon
   as ``main`` advances with anything the candidate does not already contain,
   and a divergent integration tree is genuinely different work -- so it is
   validated in full, exactly as before.
2. **The trusted run record.** The successful exact-head ``Hunter / Pre-PR
   Preflight`` push run is read from the Actions API by immutable head SHA. It
   is the same evidence the trusted default-branch controller requires for
   candidate admission, and no writer on any channel can mint it. "Latest
   green" is never consulted.
3. **The pinned toolchain.** Both runs install from the same pinned files in
   the same tree, so a runner that matches the pin matches the other run.

Everything else fails closed onto running the full lane: a different event, a
missing or unsuccessful run, unavailable Git or API evidence, a tests-first-red
head, a toolchain that does not match its pin, or a receipt that does not verify.
Refusing reuse is always safe; it costs time, not proof.

Reuse is deliberately non-blocking. A queued or in-progress trusted run is not proof yet,
so CI immediately runs its own full lane instead of polling another workflow. This keeps
validation reuse an optimization rather than a cross-workflow synchronization boundary;
correctness never depends on timing, and PR latency cannot be held by another run.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import hunter_github_transport as transport
import hunter_validation_receipt as receipts

ROOT = Path(__file__).resolve().parents[1]

#: The one hosted workflow whose successful exact-head run is full repository
#: proof. These must stay identical to the trusted default-branch controller's
#: constants; a regression test pins them together rather than importing the
#: controller into an untrusted CI job.
PRE_PR_WORKFLOW_NAME = "Hunter / Pre-PR Preflight"
PRE_PR_WORKFLOW_PATH = ".github/workflows/hunter-pre-pr-preflight.yml"

#: A tests-first-red head is a declared hygiene signal, never full proof, so a
#: head carrying the marker can never authorize skipping the full lane.
MODE_MARKER = ".hunter-preflight-mode"

#: Reuse is a non-blocking optimization, never a synchronization boundary.
#: A pending trusted run is not proof yet; CI validates independently immediately.
#: This invariant prevents cross-workflow polling from becoming PR latency.
PRODUCED_BY = "hunter-pre-pr-preflight"
RUN_PENDING_STATUSES = frozenset({"queued", "in_progress", "requested", "waiting", "pending"})

Fetch = Callable[[str, str, str], Any]


@dataclass(frozen=True)
class ReuseDecision:
    reusable: bool
    reason: str
    pending: bool = False


def _fetch(repository: str, token: str, path: str) -> Any:
    return transport.request_rest_json(
        url=f"https://api.github.com/repos/{repository}/{path}",
        method="GET",
        headers={},
        data=None,
        token=token,
        what=f"GET {path}",
    )


def trusted_branch_preflight_run(runs: Sequence[Any], head_sha: str) -> tuple[dict[str, Any] | None, str]:
    """The exact-head branch preflight run, or why none of these qualifies.

    Every field is matched, not just the name: a run of some other workflow that
    merely calls itself the same thing, a re-dispatch under a different event,
    or a run belonging to another head are all rejected rather than accepted as
    proof of this candidate.
    """
    matching = [
        run
        for run in runs
        if isinstance(run, dict)
        and str(run.get("head_sha") or "") == head_sha
        and str(run.get("name") or "") == PRE_PR_WORKFLOW_NAME
        and str(run.get("path") or "") == PRE_PR_WORKFLOW_PATH
        and str(run.get("event") or "") == "push"
    ]
    if not matching:
        return None, f"no exact-head {PRE_PR_WORKFLOW_NAME} push run exists for {head_sha}"

    latest = max(matching, key=lambda run: int(run.get("id") or 0))
    status = str(latest.get("status") or "")
    if status != "completed":
        # Only a status that is genuinely still moving is waited on. An unreadable
        # status is not evidence that the run is progressing, so it fails closed
        # instead of being treated as pending indefinitely.
        if status in RUN_PENDING_STATUSES:
            return latest, f"exact-head {PRE_PR_WORKFLOW_NAME} is {status}"
        return None, f"exact-head {PRE_PR_WORKFLOW_NAME} reports unreadable status {status or 'unknown'}"
    conclusion = str(latest.get("conclusion") or "")
    if conclusion != "success":
        return None, f"exact-head {PRE_PR_WORKFLOW_NAME} concluded {conclusion or 'unknown'}"
    return latest, ""


def _run_produced_at(run: dict[str, Any]) -> tuple[datetime | None, str]:
    raw = str(run.get("updated_at") or run.get("created_at") or "").strip()
    if not raw:
        return None, "trusted run record carries no completion time"
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, f"trusted run record completion time is unreadable: {raw!r}"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC), ""


@dataclass(frozen=True)
class CandidateEvidence:
    """The identities a reusable proof must be bound to, computed once per decision."""

    head_sha: str
    integration: str
    candidate: str
    definition: str
    toolchain: str


def candidate_evidence(root: Path, *, event_name: str, head_sha: str) -> tuple[CandidateEvidence | None, str]:
    """Everything decidable without the API, or why this candidate cannot reuse proof.

    Kept separate from the run record so a bounded wait recomputes no Git evidence:
    the tree, definition and toolchain identities cannot change while this job waits,
    so re-deriving them on every poll would cost subprocess work and prove nothing.
    """
    if event_name != "pull_request":
        return None, f"{event_name or 'unknown'} events validate their own tree in full"
    if not receipts.is_object_sha(head_sha):
        return None, "no exact candidate head SHA is available"
    if (root / MODE_MARKER).exists():
        return None, "a tests-first-red head is never full repository proof"

    try:
        integration = receipts.content_identity(root)
        candidate = receipts.commit_tree_identity(root, head_sha)
        definition = receipts.definition_identity(root)
        measured = receipts.toolchain_identity(receipts.measured_toolchain())
    except receipts.ValidationEvidenceUnavailable as exc:
        return None, f"validation identity evidence is unavailable ({exc})"

    if integration != candidate:
        return None, "the integration tree differs from the validated candidate tree, so it is different work"
    if measured != receipts.toolchain_identity(receipts.pinned_toolchain(root)):
        return None, "this runner's toolchain does not match the toolchain the tree pins"
    return CandidateEvidence(head_sha, integration, candidate, definition, measured), ""


def _authoritative_runs(
    repository: str, token: str, head_sha: str, fetch: Fetch | None
) -> tuple[list[Any] | None, str]:
    payload = (fetch or _fetch)(
        repository,
        token,
        f"actions/runs?head_sha={quote(head_sha, safe='')}&event=push&per_page=100",
    )
    if not isinstance(payload, dict):
        return None, "branch preflight run evidence is malformed"
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        return None, "workflow_runs payload is malformed"
    return runs, ""


def decide_from_runs(runs: list[Any], evidence: CandidateEvidence, *, now: datetime | None = None) -> ReuseDecision:
    """Decide reuse from the authoritative run record and the identities it must match."""
    run, problem = trusted_branch_preflight_run(runs, evidence.head_sha)
    if run is None:
        return ReuseDecision(False, problem)
    if problem:
        return ReuseDecision(False, problem, pending=True)
    produced_at, problem = _run_produced_at(run)
    if produced_at is None:
        return ReuseDecision(False, problem)

    receipt = receipts.ValidationReceipt(
        lane=receipts.FULL_LANE,
        result=receipts.PASSED,
        head_sha=evidence.head_sha,
        content_identity=evidence.candidate,
        definition_identity=evidence.definition,
        toolchain_identity=evidence.toolchain,
        produced_at=produced_at,
        produced_by=PRODUCED_BY,
    )
    expected = receipts.ValidationIdentity(
        content=evidence.integration, definition=evidence.definition, toolchain=evidence.toolchain
    )
    blocker = receipts.verify(receipt, expected, head_sha=evidence.head_sha, now=now)
    if blocker is not None:
        return ReuseDecision(False, blocker)

    return ReuseDecision(
        True,
        f"the trusted exact-head {PRE_PR_WORKFLOW_NAME} already validated this exact tree ({evidence.candidate})",
    )


def resolve(
    root: Path,
    *,
    event_name: str,
    head_sha: str,
    repository: str,
    token: str,
    now: datetime | None = None,
    fetch: Fetch | None = None,
) -> ReuseDecision:
    """Decide whether this job may stand on the trusted exact-head proof, without waiting."""
    evidence, blocker = candidate_evidence(root, event_name=event_name, head_sha=head_sha)
    if evidence is None:
        return ReuseDecision(False, blocker)
    try:
        runs, problem = _authoritative_runs(repository, token, head_sha, fetch)
    except Exception as exc:  # noqa: BLE001 - any failure here must fall back to full validation
        return ReuseDecision(False, f"reuse evidence could not be established ({type(exc).__name__}: {exc})")
    if runs is None:
        return ReuseDecision(False, problem)
    return decide_from_runs(runs, evidence, now=now)


def _emit(decision: ReuseDecision) -> None:
    print(f"[Hunter Validation Reuse] {'REUSE' if decision.reusable else 'RUN-FULL'}: {decision.reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"reusable={'true' if decision.reusable else 'false'}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Decide whether pull-request CI may reuse the trusted hosted exact-head full "
            "repository proof instead of re-running the identical suite."
        )
    )
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--head-sha", default=os.environ.get("PR_HEAD_SHA", ""))
    parser.add_argument("--repository", default=os.environ.get("GH_REPO", ""))
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        decision = resolve(
            ROOT,
            event_name=args.event_name,
            head_sha=args.head_sha,
            repository=args.repository,
            token=token,
        )
    except Exception as exc:  # noqa: BLE001 - reuse is optional; full validation is the fail-closed path
        decision = ReuseDecision(False, f"reuse resolution failed ({type(exc).__name__}: {exc})")
    _emit(decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
