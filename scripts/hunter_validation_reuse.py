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
import hashlib
import json
import os
import re
import subprocess
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
# Hosted workflow timeout is 30 minutes; an additional 15 minutes covers queue
# propagation without treating a stranded run as pending indefinitely.
PENDING_PROOF_DEADLINE_SECONDS = 45 * 60

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

    # The Actions API may return overlapping pages or contradictory snapshots.
    # Never select a successful record when the same run/attempt has competing
    # terminal or in-flight evidence: an ambiguous authority is no authority.
    identities: dict[tuple[int, int], tuple[str, str]] = {}
    try:
        for run in matching:
            identity = (int(run["id"]), int(run.get("run_attempt", 1)))
            if identity[0] <= 0 or identity[1] <= 0:
                raise ValueError("nonpositive run/attempt")
            outcome = (str(run.get("status") or ""), str(run.get("conclusion") or ""))
            if identity in identities and identities[identity] != outcome:
                return None, "conflicting authoritative snapshots for one run/attempt"
            identities[identity] = outcome
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, "exact-head proof run/attempt identity is malformed"

    # A rerun of one Actions run increments run_attempt without changing id.
    # An older successful attempt must never authorize a newer failed attempt.
    try:
        latest = max(
            matching,
            key=lambda run: (int(run.get("id")), int(run.get("run_attempt", 1))),
        )
        if int(latest.get("id")) <= 0 or int(latest.get("run_attempt", 1)) <= 0:
            raise ValueError("nonpositive run identity")
    except (TypeError, ValueError, OverflowError):
        return None, "exact-head proof run/attempt identity is malformed"
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
        # Pending is not an unlimited permission to keep readiness unresolved.
        # A timed-out or unreadable run remains fail-closed and actionable.
        # A rerun keeps the original created_at. Measure the current
        # attempt instead, or a fresh rerun is falsely declared timed out.
        attempt = int(run.get("run_attempt", 1))
        status = str(run.get("status") or "")
        raw = str(run.get("run_started_at") or "").strip()
        if not raw:
            if attempt == 1:
                raw = str(run.get("created_at") or run.get("updated_at") or "").strip()
            elif status in {"queued", "requested", "waiting", "pending"}:
                raw = str(run.get("updated_at") or "").strip()
        try:
            started = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if started.tzinfo is None:
                raise ValueError("missing timezone")
            elapsed = ((now or datetime.now(UTC)) - started.astimezone(UTC)).total_seconds()
        except ValueError:
            return ReuseDecision(False, "exact-head proof pending timestamp is unavailable")
        if elapsed < -60 or elapsed > PENDING_PROOF_DEADLINE_SECONDS:
            return ReuseDecision(False, "exact-head proof exceeded bounded pending deadline")
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


def _canonical_job_proof(
    repository: str,
    token: str,
    run: dict[str, Any],
    head_sha: str,
    fetch: Fetch | None,
    jobs_payload: dict[str, Any] | None = None,
) -> tuple[str | None, str]:
    """Re-derive one immutable outcome from GitHub's trusted run and job records.

    GitHub Actions is the producer; this is a read-only canonical receipt, not
    a candidate-authored status or a second proof publisher.
    """
    try:
        run_id = int(run["id"])
        attempt = int(run.get("run_attempt", 1))
        if run_id <= 0 or attempt <= 0:
            raise ValueError("nonpositive run identity")
        # One validated snapshot supplies both the digest claims and the job
        # identity. Callers that already fetched the jobs pass that exact
        # snapshot here so a second, contradictory API read cannot authorize
        # reuse.
        payload = jobs_payload
        if payload is None:
            payload = (fetch or _fetch)(
                repository, token, f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return None, "trusted proof job evidence is malformed"
        jobs = [
            job for job in payload["jobs"] if isinstance(job, dict) and job.get("name") == "Hunter Pre-PR Preflight"
        ]
        if len(jobs) != 1:
            return None, "trusted proof must have exactly one authoritative preflight job"
        job = jobs[0]
        job_id = int(job["id"])
        if job_id <= 0 or job.get("run_id") != run_id:
            return None, "trusted proof job/run binding is invalid"
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            return None, "trusted proof authoritative job did not succeed"
        claims = {
            "schema": "hunter.canonical-proof-outcome/1",
            "repository": repository,
            "head_sha": head_sha,
            "workflow": PRE_PR_WORKFLOW_PATH,
            "run_id": run_id,
            "run_attempt": attempt,
            "job_id": job_id,
            "conclusion": "success",
            "provenance": "github-actions-api",
        }
        digest = hashlib.sha256(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return f"sha256:{digest}", ""
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return None, f"trusted proof job identity is invalid ({type(exc).__name__})"


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
    decision = decide_from_runs(runs, evidence, now=now)
    if not decision.reusable:
        return decision
    run, problem = trusted_branch_preflight_run(runs, head_sha)
    if run is None or problem:
        return ReuseDecision(False, problem or "trusted proof disappeared")
    try:
        digest, blocker = _canonical_job_proof(repository, token, run, head_sha, fetch)
    except Exception as exc:  # noqa: BLE001 - missing hosted job evidence never authorizes reuse
        return ReuseDecision(False, f"trusted proof job evidence unavailable ({type(exc).__name__})")
    if digest is None:
        return ReuseDecision(False, blocker)
    return ReuseDecision(True, f"{decision.reason}; canonical proof outcome digest={digest}")


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
    parser.add_argument(
        "--require-proof",
        action="store_true",
        help="Act as a proof-only consumer: return nonzero until exact-head proof exists; never run or wait for validation.",
    )
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
    if args.require_proof and not decision.reusable:
        # Fail closed without duplicating the heavy lane. A push/synchronize event
        # re-evaluates this cheap consumer on the next immutable candidate; merge
        # authority remains blocked until canonical proof is observable.
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --- Issue #606: cross-HEAD proof equivalence -------------------------------

#: All identities that must agree for cross-HEAD proof equivalence.
#: The old HEAD's success is never copied; a new decision is issued bound
#: to the current HEAD and referencing the original immutable proof.
EQUIVALENCE_IDENTITIES: tuple[str, ...] = (
    "repository",
    "pr",
    "current_head",
    "source_proof_head",
    "base_sha",
    "tested_tree_digest",
    "dependency_identity",
    "workflow_definition_identity",
    "toolchain_identity",
    "environment_identity",
    "trusted_workflow",
    "run_attempt",
    "job_identity",
    "proof_digest",
    "provenance",
)

#: Paths whose content is lifecycle metadata, not validated application code or
#: definitions. A metadata-only HEAD change alters these files but not the
#: tested inputs, so the tested subtree digest excludes them. Excluding them is
#: what lets a metadata-only candidate be proven equivalent to its source proof.
REVIEW_METADATA_PATHS: frozenset[str] = frozenset(
    {
        ".hunter/pre-ready-hostile-review.json",
    }
)


@dataclass(frozen=True)
class EquivalenceBinding:
    """All identities that must match for cross-HEAD proof equivalence."""

    repository: str
    pr_number: int
    current_head: str
    source_proof_head: str
    base_sha: str
    tested_tree_digest: str
    dependency_identity: str
    workflow_definition_identity: str
    toolchain_identity: str
    environment_identity: str
    trusted_workflow: str
    run_attempt: int
    job_identity: str
    proof_digest: str
    provenance: str


@dataclass(frozen=True)
class EquivalenceDecision:
    equivalent: bool
    reason: str
    binding: EquivalenceBinding | None = None


_HEX_SHA = re.compile(r"\A[0-9a-f]{40}\Z")


def compute_tested_tree_digest(root: Path, head_sha: str) -> tuple[str | None, str]:
    """Content-addressed digest of the *tested* subtree at ``head_sha``.

    Review-request lifecycle metadata is excluded: it is not an input to the
    heavy validation suite, so a metadata-only HEAD change leaves this digest
    unchanged. Every other path -- application code, tests, dependencies,
    workflows, trust roots, security definitions -- is included, so any change
    to a validated input changes the digest and equivalence fails closed.

    The digest is derived from Git's own blob object IDs (``git ls-tree``), so
    it is cryptographically bound to exact content rather than to a file name
    or a caller-supplied label. Any Git/parse failure fails closed.
    """
    try:
        completed = subprocess.run(
            ("git", "ls-tree", "-r", "-z", head_sha),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "git ls-tree failed"
            return None, f"tested tree digest unavailable ({detail})"
        entries: list[tuple[bytes, bytes]] = []
        for record in completed.stdout.split("\0"):
            if not record:
                continue
            if "\t" not in record:
                return None, "tested tree digest unavailable (malformed ls-tree record)"
            meta, path = record.split("\t", 1)
            if path in REVIEW_METADATA_PATHS:
                continue
            entries.append((path.encode("utf-8"), meta.encode("utf-8")))
        if not entries:
            return None, "tested tree digest unavailable (empty tested subtree)"
        entries.sort()
        hasher = hashlib.sha256()
        for path, meta in entries:
            hasher.update(path)
            hasher.update(b"\0")
            hasher.update(meta)
            hasher.update(b"\n")
        return f"sha256:{hasher.hexdigest()}", ""
    except (OSError, ValueError) as exc:
        return None, f"tested tree digest unavailable ({type(exc).__name__}: {exc})"


def compute_dependency_identity(root: Path) -> tuple[str | None, str]:
    """The dependency/lockfile identity: pinned CI constraints file."""
    try:
        constraints = root / "requirements" / "ci-constraints.txt"
        if not constraints.is_file():
            return None, "dependency lockfile unavailable"
        payload = constraints.read_bytes()
        return hashlib.sha256(payload).hexdigest(), ""
    except OSError as exc:
        return None, f"dependency identity unavailable ({exc})"


def compute_workflow_definition_identity(root: Path) -> tuple[str | None, str]:
    """The workflow and validation-definition identity."""
    try:
        import hunter_pr_preflight as preflight

        parts: list[str] = []
        for relative in (
            PRE_PR_WORKFLOW_PATH,
            ".github/workflows/hunter-trusted-preflight-upgrade.yml",
            ".github/workflows/ci.yml",
        ):
            path = root / relative
            payload = path.read_bytes()
            parts.append(f"file:{relative}:{hashlib.sha256(payload).hexdigest()}")
        parts.append(f"gate-chain:{hashlib.sha256(repr(preflight.NORMAL_QUALITY_GATES).encode()).hexdigest()}")
        return hashlib.sha256("|".join(parts).encode()).hexdigest(), ""
    except (OSError, ImportError) as exc:
        return None, f"workflow definition identity unavailable ({exc})"


def compute_environment_identity(root: Path) -> tuple[str | None, str]:
    """The relevant environment identity: Python version and workflow pins."""
    try:
        import sys

        parts: list[str] = [f"python:{'.'.join(str(p) for p in sys.version_info[:3])}"]
        for workflow in (".github/workflows/ci.yml", PRE_PR_WORKFLOW_PATH):
            path = root / workflow
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in content.splitlines():
                if "python-version" in line:
                    parts.append(f"workflow:{workflow}:{line.strip()}")
        return hashlib.sha256("|".join(parts).encode()).hexdigest(), ""
    except Exception as exc:  # noqa: BLE001 - environment identity is best-effort but must exist
        return None, f"environment identity unavailable ({exc})"


def compute_provenance(root: Path, head_sha: str) -> tuple[str | None, str]:
    """Provenance: the exact commit SHA and its tree object."""
    try:
        tree_sha = receipts.commit_tree_identity(root, head_sha)
        return f"commit:{head_sha}:tree:{tree_sha}", ""
    except receipts.ValidationEvidenceUnavailable as exc:
        return None, f"provenance unavailable ({exc})"


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """Whether ``ancestor`` is an ancestor commit of ``descendant`` in Git."""
    completed = subprocess.run(
        ("git", "merge-base", "--is-ancestor", ancestor, descendant),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def find_previous_proven_head(repository: str, token: str, current_head: str) -> tuple[str | None, str]:
    """Find an ancestral HEAD whose successful exact-head proof proves this tree.

    The Actions API is searched for *all* successful Hunter / Pre-PR Preflight
    push runs, not just the globally newest one. A candidate source proof is
    eligible only when it is an ancestor of the current HEAD, its tested subtree
    digest equals the current HEAD's, and its run/attempt/job and freshness are
    re-derived by the canonical proof authority. The globally newest successful
    run on an unrelated branch is never selected.

    The old HEAD's success is never copied as the new HEAD's proof; this only
    locates the immutable source proof to reference in a new decision.
    """
    try:
        payload = _fetch(repository, token, "actions/runs?event=push&per_page=100")
        if not isinstance(payload, dict):
            return None, "run evidence is malformed"
        runs = payload.get("workflow_runs")
        if not isinstance(runs, list):
            return None, "workflow_runs payload is malformed"
        if not _HEX_SHA.fullmatch(current_head):
            return None, "current head is not an exact 40-hex commit SHA"

        proven_heads: dict[str, int] = {}
        for run in runs:
            if not isinstance(run, dict):
                continue
            if str(run.get("name") or "") != "Hunter / Pre-PR Preflight":
                continue
            if str(run.get("path") or "") != PRE_PR_WORKFLOW_PATH:
                continue
            if str(run.get("event") or "") != "push":
                continue
            if str(run.get("status") or "") != "completed" or str(run.get("conclusion") or "") != "success":
                continue
            head_sha = str(run.get("head_sha") or "")
            if head_sha == current_head or not _HEX_SHA.fullmatch(head_sha):
                continue
            run_id = int(run.get("id", 0))
            if run_id <= 0:
                continue
            proven_heads[head_sha] = max(proven_heads.get(head_sha, 0), run_id)

        if not proven_heads:
            return None, "no previous successful exact-head proof found"

        current_tree, problem = compute_tested_tree_digest(ROOT, current_head)
        if problem or current_tree is None:
            return None, f"current tested tree for source selection is unavailable ({problem})"

        for source_head, _run_id in sorted(proven_heads.items(), key=lambda item: item[1], reverse=True):
            if not _is_ancestor(ROOT, source_head, current_head):
                continue
            source_tree, problem = compute_tested_tree_digest(ROOT, source_head)
            if problem or source_tree != current_tree:
                continue
            attempt, job_id, digest, problem = _source_proof_bindings(repository, token, source_head)
            if problem or attempt <= 0 or job_id <= 0 or not digest:
                continue
            return source_head, ""
        return None, "no ancestral source proof with an identical tested tree and fresh canonical bindings"
    except Exception as exc:  # noqa: BLE001 - any failure fails closed
        return None, f"previous proof search failed ({type(exc).__name__}: {exc})"


def _commit_has_red_mode(root: Path, head_sha: str) -> tuple[bool, str]:
    """Check the committed RED marker, never just the checkout working tree.

    A successful tests-first-red run proves that tests FAILED, not that the
    full suite passed. Missing Git evidence must not authorize proof reuse.
    """
    result = subprocess.run(
        ("git", "ls-tree", "--name-only", head_sha, "--", MODE_MARKER),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, "tests-first-red mode evidence unavailable"
    return MODE_MARKER in result.stdout.splitlines(), ""


def verify_equivalence(
    root: Path,
    *,
    repository: str,
    pr_number: int,
    current_head: str,
    source_proof_head: str,
    base_sha: str,
    token: str,
    run_attempt: int,
    job_id: int,
    proof_digest: str,
) -> EquivalenceDecision:
    """Verify all cross-HEAD equivalence bindings and issue a new decision.

    The old HEAD's success is never copied as the new HEAD's proof. A new
    current-HEAD-bound decision is issued that references the original
    immutable proof.
    """
    # Source proof must be normal full validation, not an expected-RED run.
    # Check BOTH committed HEADs: the metadata-only successor inherits the
    # same tested tree, but must never promote an expected failure to success.
    for proof_head in (source_proof_head, current_head):
        red_mode, mode_problem = _commit_has_red_mode(root, proof_head)
        if mode_problem:
            return EquivalenceDecision(False, mode_problem)
        if red_mode:
            return EquivalenceDecision(False, "tests-first-red head is never full repository proof")
    if (root / MODE_MARKER).exists():
        return EquivalenceDecision(False, "tests-first-red working tree is never full repository proof")

    bindings: dict[str, str] = {}

    # 1. Repository identity.
    bindings["repository"] = repository

    # 2. PR identity.
    bindings["pr"] = str(pr_number)

    # 3. Current exact HEAD.
    bindings["current_head"] = current_head

    # 4. Immutable source proof HEAD.
    bindings["source_proof_head"] = source_proof_head

    # 5. Base SHA.
    bindings["base_sha"] = base_sha

    # 6. Tested tree/subtree digest - must match between source and current.
    current_tree, problem = compute_tested_tree_digest(root, current_head)
    if problem:
        return EquivalenceDecision(False, f"current tested tree: {problem}")
    bindings["tested_tree_digest"] = current_tree

    source_tree, problem = compute_tested_tree_digest(root, source_proof_head)
    if problem:
        return EquivalenceDecision(False, f"source tested tree: {problem}")
    bindings["source_tested_tree_digest"] = source_tree

    if current_tree != source_tree:
        return EquivalenceDecision(
            False,
            f"tested tree differs between source proof head {source_proof_head[:10]} "
            f"and current head {current_head[:10]}",
        )

    # 7. Dependency/lockfile identity.
    dep_identity, problem = compute_dependency_identity(root)
    if problem:
        return EquivalenceDecision(False, f"dependency identity: {problem}")
    bindings["dependency_identity"] = dep_identity

    # 8. Workflow and validation-definition identity.
    wf_identity, problem = compute_workflow_definition_identity(root)
    if problem:
        return EquivalenceDecision(False, f"workflow definition: {problem}")
    bindings["workflow_definition_identity"] = wf_identity

    # 9. Toolchain identity - runner must match pinned toolchain.
    try:
        toolchain = receipts.toolchain_identity(receipts.measured_toolchain())
        pinned = receipts.toolchain_identity(receipts.pinned_toolchain(root))
        if toolchain != pinned:
            return EquivalenceDecision(False, "runner toolchain does not match pinned toolchain")
        bindings["toolchain_identity"] = toolchain
    except receipts.ValidationEvidenceUnavailable as exc:
        return EquivalenceDecision(False, f"toolchain identity: {exc}")

    # 10. Environment identity.
    env_identity, problem = compute_environment_identity(root)
    if problem:
        return EquivalenceDecision(False, f"environment identity: {problem}")
    bindings["environment_identity"] = env_identity

    # 11. Trusted workflow path.
    bindings["trusted_workflow"] = PRE_PR_WORKFLOW_PATH

    # 12. Run/attempt - must be positive.
    if run_attempt <= 0:
        return EquivalenceDecision(False, "run_attempt must be positive")
    bindings["run_attempt"] = str(run_attempt)

    # 13. Job identity - must be positive.
    if job_id <= 0:
        return EquivalenceDecision(False, "job_id must be positive")
    bindings["job_identity"] = f"job:{job_id}"

    # 14. Proof digest - references the original immutable proof.
    if not proof_digest:
        return EquivalenceDecision(False, "proof digest is required for equivalence")
    bindings["proof_digest"] = proof_digest

    # 15. Provenance - current head commit and tree.
    provenance, problem = compute_provenance(root, current_head)
    if problem:
        return EquivalenceDecision(False, f"provenance: {problem}")
    bindings["provenance"] = provenance

    # Issue a new current-HEAD-bound decision referencing the original proof.
    binding = EquivalenceBinding(
        repository=bindings["repository"],
        pr_number=pr_number,
        current_head=current_head,
        source_proof_head=source_proof_head,
        base_sha=base_sha,
        tested_tree_digest=bindings["tested_tree_digest"],
        dependency_identity=bindings["dependency_identity"],
        workflow_definition_identity=bindings["workflow_definition_identity"],
        toolchain_identity=bindings["toolchain_identity"],
        environment_identity=bindings["environment_identity"],
        trusted_workflow=bindings["trusted_workflow"],
        run_attempt=run_attempt,
        job_identity=bindings["job_identity"],
        proof_digest=bindings["proof_digest"],
        provenance=bindings["provenance"],
    )

    return EquivalenceDecision(
        True,
        f"cross-HEAD proof equivalence verified: current head {current_head[:10]} bound to "
        f"source proof head {source_proof_head[:10]}",
        binding=binding,
    )


def _source_proof_bindings(
    repository: str,
    token: str,
    source_head: str,
    *,
    now: datetime | None = None,
) -> tuple[int, int, str, str]:
    """Derive the authoritative run/attempt, job and proof digest for the source proof.

    The digest is produced by the existing Canonical Proof Outcome Authority
    (``hunter_validation_reuse._canonical_job_proof``); this module never mints
    its own proof. Any missing, ambiguous, stale or invalid hosted job evidence
    fails closed. Freshness is enforced by the canonical receipt verifier over
    the canonical completion time, never by a caller-supplied age estimate.
    """
    if not _HEX_SHA.fullmatch(source_head):
        return 0, 0, "", "source proof head is not an exact 40-hex commit SHA"
    try:
        payload = _fetch(
            repository, token, f"actions/runs?head_sha={quote(source_head, safe='')}&event=push&per_page=100"
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
            return 0, 0, "", "source proof run evidence is malformed"
        run, problem = trusted_branch_preflight_run(payload["workflow_runs"], source_head)
        if run is None or problem:
            return 0, 0, "", f"source proof run is not authoritative ({problem})"
        produced_at, problem = _run_produced_at(run)
        if produced_at is None:
            return 0, 0, "", f"source proof completion time is unavailable ({problem})"
        freshness_receipt = receipts.ValidationReceipt(
            lane=receipts.FULL_LANE,
            result=receipts.PASSED,
            head_sha=source_head,
            content_identity="",
            definition_identity="",
            toolchain_identity="",
            produced_at=produced_at,
            produced_by=PRODUCED_BY,
        )
        expected = receipts.ValidationIdentity(content="", definition="", toolchain="")
        blocker = receipts.verify(freshness_receipt, expected, head_sha=source_head, now=now)
        if blocker is not None:
            return 0, 0, "", f"source proof freshness refused: {blocker}"
        attempt = int(run.get("run_attempt", 1))
        run_id = int(run["id"])
        # A single jobs snapshot is fetched and reused for both the canonical
        # digest and the authoritative job identity. Contradictory successive
        # reads are therefore structurally impossible on this path.
        jobs_payload = _fetch(repository, token, f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
        if not isinstance(jobs_payload, dict) or not isinstance(jobs_payload.get("jobs"), list):
            return 0, 0, "", "source proof job evidence is malformed"
        digest, blocker = _canonical_job_proof(repository, token, run, source_head, None, jobs_payload=jobs_payload)
        if not digest:
            return 0, 0, "", f"canonical proof outcome digest unavailable ({blocker})"
        authoritative_jobs = [
            job
            for job in jobs_payload["jobs"]
            if isinstance(job, dict) and job.get("name") == "Hunter Pre-PR Preflight"
        ]
        if len(authoritative_jobs) != 1:
            return 0, 0, "", "source proof must have exactly one authoritative preflight job"
        job_id = int(authoritative_jobs[0]["id"])
        if job_id <= 0 or authoritative_jobs[0].get("run_id") != run_id:
            return 0, 0, "", "source proof job/run binding is invalid"
        return attempt, job_id, digest, ""
    except Exception as exc:  # noqa: BLE001 - any failure fails closed
        return 0, 0, "", f"source proof bindings unavailable ({type(exc).__name__}: {exc})"


def verify_for_candidate(
    root: Path,
    *,
    repository: str,
    pr_number: int,
    current_head: str,
    base_sha: str,
    token: str,
    source_proof_head: str | None = None,
) -> EquivalenceDecision:
    """Verify cross-HEAD equivalence for a metadata-only candidate HEAD.

    This is the enforcement entry point used by the trusted reuse decision. It
    locates (or is given) the immutable source proof HEAD, derives that proof's
    authoritative run/attempt/job/digest from the *existing* canonical proof
    authority, and verifies every equivalence identity against the current
    HEAD. A successful result is a NEW current-HEAD-bound decision referencing
    the original proof -- never a copied old-HEAD success.
    """
    if not _HEX_SHA.fullmatch(current_head):
        return EquivalenceDecision(False, "current head is not an exact 40-hex commit SHA")
    if not _HEX_SHA.fullmatch(base_sha):
        return EquivalenceDecision(False, "base SHA is not an exact 40-hex commit SHA")
    if pr_number <= 0:
        return EquivalenceDecision(False, "PR number must be positive")
    if source_proof_head is not None and not _HEX_SHA.fullmatch(source_proof_head):
        return EquivalenceDecision(False, "source proof head is not an exact 40-hex commit SHA")

    if source_proof_head is None:
        source_proof_head, problem = find_previous_proven_head(repository, token, current_head)
        if problem:
            return EquivalenceDecision(False, f"no source proof: {problem}")

    # Caller-supplied labels are never trusted as proof: the canonical authority
    # derives the source run/attempt/job/digest from immutable hosted evidence.
    attempt, job_id, digest, problem = _source_proof_bindings(repository, token, source_proof_head)
    if problem:
        return EquivalenceDecision(False, problem)

    return verify_equivalence(
        root,
        repository=repository,
        pr_number=pr_number,
        current_head=current_head,
        source_proof_head=source_proof_head,
        base_sha=base_sha,
        token=token,
        run_attempt=attempt,
        job_id=job_id,
        proof_digest=digest,
    )
