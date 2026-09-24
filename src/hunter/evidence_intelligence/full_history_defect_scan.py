"""Deterministic full-history defect backfill worker.

The full-history worker captures a frozen GitHub snapshot of *every* pull
request in a repository and converts trustworthy historical review/finding
knowledge into Hunter's canonical learning pipeline:

    raw evidence -> neutral observations -> deterministic disposition
    -> canonical learning ledger (hunter-learning-ledger-v1)
    -> KnowledgeExtractionAuthority proposals -> awaiting-governed-validation queue

The scanner is:

- deterministic (stable ordering, stable digests, no live model input);
- batched and checkpointed (survives interruption, resumes idempotently);
- provider-independent (GitHub transport failure is excluded explicitly and
  never becomes defect authority; the scanner itself never calls an LLM);
- governed-fail-closed (raw review prose is evidence only; classification
  never invents a defect family, and canonical writes are never authorized);

Every PR existing at the final GitHub snapshot receives exactly one explicit
auditable status so coverage_gap == 0 at completion.

Authority rules (see docs/superpowers/specs/2026-09-23-*.md):

- Historical backfill must enter through the same governed event contract as
  the incremental learner (event-ingestion design rule 10).
- confirmed/false-positive authority is never invented from prose; only the
  owner-validated HISTORICAL_DEFECT_BACKFILL records and explicit governed
  classification decisions can classify an observation as a defect.
- deterministic exclusions (style, obsolete, false-positive-by-owner-reply,
  provider/infra unavailability) are the only automatic dispositions.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hunter.evidence_intelligence.incremental_knowledge_learning import build_learning_ledger

SCHEMA_VERSION = "hunter-full-history-defect-scan-v1"
SNAPSHOT_SCHEMA_VERSION = "hunter-full-history-snapshot-v1"
PROGRESS_SCHEMA_VERSION = "hunter-full-history-progress-v1"
COVERAGE_SCHEMA_VERSION = "hunter-full-history-coverage-manifest-v1"

DEFAULT_BATCH_SIZE = 20
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_PAGES = 5
_PER_PAGE = 100

# ---------------------------------------------------------------------------
# Per-PR explicit auditable statuses (mission section 5). Every PR in the final
# scope ends in exactly one of these; status_count_total == total_prs_in_scope.
# ---------------------------------------------------------------------------

PR_STATUS_SCANNED_NO_FINDING = "SCANNED_NO_FINDING"
PR_STATUS_FINDING_EXTRACTED = "FINDING_EXTRACTED"
PR_STATUS_CONFIRMED_DEFECT = "CONFIRMED_DEFECT"
PR_STATUS_DUPLICATE_DEFECT = "DUPLICATE_DEFECT"
PR_STATUS_FALSE_POSITIVE = "FALSE_POSITIVE"
PR_STATUS_STYLE_OR_NON_DEFECT = "STYLE_OR_NON_DEFECT"
PR_STATUS_OUT_OF_SCOPE = "OUT_OF_SCOPE"
PR_STATUS_INFRA_PROVIDER_FAILURE = "INFRA_PROVIDER_FAILURE"
PR_STATUS_UNRESOLVED = "UNRESOLVED"

PR_STATUSES = frozenset(
    {
        PR_STATUS_SCANNED_NO_FINDING,
        PR_STATUS_FINDING_EXTRACTED,
        PR_STATUS_CONFIRMED_DEFECT,
        PR_STATUS_DUPLICATE_DEFECT,
        PR_STATUS_FALSE_POSITIVE,
        PR_STATUS_STYLE_OR_NON_DEFECT,
        PR_STATUS_OUT_OF_SCOPE,
        PR_STATUS_INFRA_PROVIDER_FAILURE,
        PR_STATUS_UNRESOLVED,
    }
)

# ---------------------------------------------------------------------------
# Observation classifications. These are exactly the values the canonical event
# contract and KnowledgeExtractionAuthority accept (only ``confirmed`` may ever
# become defect knowledge; everything else is an explicit exclusion).
# ---------------------------------------------------------------------------

CLASS_CONFIRMED = "confirmed"
CLASS_FALSE_POSITIVE = "false-positive"
CLASS_STYLE = "style"
CLASS_OBSOLETE = "obsolete"
CLASS_INFRASTRUCTURE = "infrastructure"
CLASS_PROVIDER_UNAVAILABLE = "provider-unavailable"

EVENT_CLASSIFICATIONS = frozenset(
    {
        CLASS_CONFIRMED,
        CLASS_FALSE_POSITIVE,
        CLASS_STYLE,
        CLASS_OBSOLETE,
        CLASS_INFRASTRUCTURE,
        CLASS_PROVIDER_UNAVAILABLE,
    }
)

# ---------------------------------------------------------------------------
# Per-PR scan-state machine. scan_state is how the worker survives provider
# outages without losing work: infra failures are recorded, bounded-retried
# across resumes, and only become the explicit INFRA_PROVIDER_FAILURE status
# after the bounded attempt limit.
# ---------------------------------------------------------------------------

SCAN_COMPLETE = "complete"
SCAN_INFRA_RETRYABLE = "infra-retryable"
SCAN_INFRA_PERMANENT = "infra-permanent"
SCAN_PERMANENT_ERROR = "permanent-error"

SCAN_STATES = frozenset({SCAN_COMPLETE, SCAN_INFRA_RETRYABLE, SCAN_INFRA_PERMANENT, SCAN_PERMANENT_ERROR})


class ScanInfrastructureUnavailable(RuntimeError):
    """GitHub/provider infrastructure was unavailable for one pull request."""


class ScanPermanentError(RuntimeError):
    """Evidence for one pull request cannot be collected (semantic failure)."""


class FullHistoryScanError(ValueError):
    """Raised for a systemic configuration or data-integrity failure."""


# REST request shape shared with the Hunter governance surface: the caller
# passes a function compatible with hunter_governance_review_v2.request_json.
RestRequest = Callable[[str, str, str, str], Any]

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Deterministic owner-reply disposition markers. These apply only when the
# reply author is the repository owner, so a plain reviewer opinion can never
# self-dispose its own finding.
_FALSE_POSITIVE_MARKERS = (
    "not a bug",
    "not a defect",
    "not a real defect",
    "false positive",
    "intended behavior",
    "by design",
    "works as intended",
    "won't fix",
    "wont fix",
    "wontfix",
    "cannot reproduce",
    "can't reproduce",
    "out of scope",
    "no issue here",
)

_STYLE_OR_NON_DEFECT_MARKERS = (
    "nit",
    "nitpick",
    "style",
    "cosmetic",
    "formatting only",
    "typo",
    "naming suggestion",
    "non-blocking",
    "non blocking",
    "preference",
    "optional suggestion",
    "refactor only",
    "readability only",
)


def utc_now_iso() -> str:
    """Deterministic UTC ISO-8601 timestamp for evidence/checkpoint records."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def stable_json_digest(value: Any) -> str:
    """Canonical JSON digest over any value (sort_keys, compact separators)."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    """Write a JSON value to *path* atomically (temp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_json_file(path: Path, *, required: bool = False) -> dict[str, Any] | None:
    if not path.is_file():
        if required:
            raise FullHistoryScanError(f"{path} is required but missing")
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FullHistoryScanError(f"{path} must contain a JSON object")
    return data


# ---------------------------------------------------------------------------
# Frozen snapshot capture
# ---------------------------------------------------------------------------


def snapshot_pr_summaries(payloads: list[Any]) -> list[dict[str, Any]]:
    """Extract immutable PR identifiers from raw pagination payloads.

    The summary carries every identifier the final coverage manifest needs and
    nothing GitHub may later mutate in a way that changes identity.
    """
    summaries: list[dict[str, Any]] = []
    for page in payloads:
        if not isinstance(page, list):
            raise FullHistoryScanError("pull request pagination payload must be a list")
        for item in page:
            if not isinstance(item, dict):
                raise FullHistoryScanError("pull request entry must be an object")
            number = item.get("number")
            if not isinstance(number, int) or number <= 0:
                raise FullHistoryScanError("pull request number is invalid")
            user = item.get("user") if isinstance(item.get("user"), dict) else {}
            head = item.get("head") if isinstance(item.get("head"), dict) else {}
            base = item.get("base") if isinstance(item.get("base"), dict) else {}
            summaries.append(
                {
                    "number": number,
                    "title": str(item.get("title") or ""),
                    "state": str(item.get("state") or ""),
                    "is_draft": bool(item.get("draft") or False),
                    "merged": bool(item.get("merged_at") is not None),
                    "merged_at": item.get("merged_at"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "closed_at": item.get("closed_at"),
                    "author": str(user.get("login") or ""),
                    "head_sha": str(head.get("sha") or ""),
                    "head_ref": str(head.get("ref") or ""),
                    "base_sha": str(base.get("sha") or ""),
                    "base_ref": str(base.get("ref") or ""),
                    "merge_commit_sha": item.get("merge_commit_sha"),
                }
            )
    return sorted(summaries, key=lambda summary: summary["number"])


def capture_snapshot(
    request: RestRequest,
    repository: str,
    token: str,
    *,
    captured_at: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict[str, Any]:
    """Capture a deterministic frozen snapshot of every PR (open/closed/merged/draft).

    PR numbering is never assumed contiguous; the snapshot is the authoritative
    scope for the scan. This is strictly read-only (``state=all`` list query).
    """
    payloads: list[Any] = []
    for page in range(1, max_pages + 1):
        payload = request(repository, token, "GET", f"pulls?state=all&per_page={_PER_PAGE}&page={page}")
        if not isinstance(payload, list):
            raise FullHistoryScanError("pull request list payload must be a list")
        payloads.append(payload)
        if len(payload) < _PER_PAGE:
            break
    else:
        raise FullHistoryScanError(
            f"pull request snapshot exceeds {max_pages} pages; raise --max-pages to capture full history"
        )
    summaries = snapshot_pr_summaries(payloads)
    identity = {
        "repository": repository,
        "total_prs": len(summaries),
        "prs": summaries,
    }
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "repository": repository,
        "captured_at_utc": captured_at or utc_now_iso(),
        "total_prs": len(summaries),
        "snapshot_id": stable_json_digest(identity),
        "prs": summaries,
    }


def partition_batches(pr_numbers: list[int], batch_size: int) -> list[list[int]]:
    """Deterministic batched partitioning in ascending PR-number order."""
    if not isinstance(batch_size, int) or batch_size <= 0:
        raise FullHistoryScanError("batch size must be a positive integer")
    ordered = sorted(set(pr_numbers))
    return [ordered[index : index + batch_size] for index in range(0, len(ordered), batch_size)]


# ---------------------------------------------------------------------------
# Raw evidence collection (Stage A - extraction, fully neutral)
# ---------------------------------------------------------------------------


def collect_pr_evidence(
    request: RestRequest,
    repository: str,
    token: str,
    pr_number: int,
    *,
    collect_statuses: bool,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict[str, Any]:
    """Collect immutable raw evidence for one PR without classifying anything.

    Layout: pulls/{n} (metadata+head/base), commits, review comments, reviews,
    files, issue comments, and -- optionally -- the head commit statuses. All
    payloads are preserved verbatim so derived knowledge can point back to
    immutable provenance.
    """
    paginated: dict[str, list[Any]] = {}

    def collect_page(stub: str) -> list[Any]:
        out: list[Any] = []
        for page in range(1, max_pages + 1):
            payload = request(repository, token, "GET", f"{stub}?per_page={_PER_PAGE}&page={page}")
            if not isinstance(payload, list):
                raise FullHistoryScanError(f"{stub} payload must be a list")
            out.extend(payload)
            if len(payload) < _PER_PAGE:
                break
        else:
            raise FullHistoryScanError(f"{stub} evidence exceeds {max_pages} pages; raise --max-pages")
        return out

    for endpoint in ("commits", "comments", "reviews", "files"):
        paginated[endpoint] = collect_page(f"pulls/{pr_number}/{endpoint}")
    paginated["issue_comments"] = collect_page(f"issues/{pr_number}/comments")

    metadata = request(repository, token, "GET", f"pulls/{pr_number}")
    if not isinstance(metadata, dict):
        raise FullHistoryScanError("pull request metadata payload must be an object")

    statuses: list[Any] = []
    if collect_statuses:
        head = metadata.get("head")
        head_sha = head.get("sha") if isinstance(head, dict) else None
        if isinstance(head_sha, str) and head_sha:
            statuses = collect_page(f"commits/{head_sha}/status")

    return {
        "metadata": metadata,
        "commits": paginated["commits"],
        "review_comments": paginated["comments"],
        "reviews": paginated["reviews"],
        "files": paginated["files"],
        "issue_comments": paginated["issue_comments"],
        "statuses": statuses,
    }


def _neutral_observation(
    pr_number: int,
    head: str,
    base: str,
    *,
    event_id: str,
    reviewer: str,
    message: str,
    path: Any,
    line: Any,
) -> dict[str, Any]:
    """Build one neutral observation in the canonical ledger observation schema."""
    normalized_line = line if isinstance(line, int) and line > 0 else None
    return {
        "source": "github-review",
        "provider": "github-review",
        "event_id": event_id,
        "source_pr": pr_number,
        "reviewed_head_sha": head,
        "reviewed_base_sha": base,
        "reviewer": reviewer,
        "path": path,
        "line": normalized_line,
        "message": message,
        "availability": "available",
        "classification": None,
        "invariant": None,
        "affected_paths": [],
        "fix_reference": None,
        "regression_evidence": [],
        "claimed_family_id": None,
    }


def extract_observations(
    pr_number: int,
    head: str,
    base: str,
    evidence: dict[str, Any],
    *,
    owner_dispositions: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Stage A: convert raw review evidence into neutral observations.

    Inline review comments and top-level reviews (ALL historical review
    summary bodies, including those from earlier heads) carry reviewer
    findings. The observation's ``classification`` stays ``None`` unless the
    repository owner already explicitly dispositioned the comment thread (the
    only deterministic disposition the scanner may assert). Issue comments are
    evidence only and never become finding observations.

    Top-level reviews are not filtered to the current head: this is a full-
    history worker, so a condensed review from any point in the PR's life is
    historical evidence about this PR. Each observation is still bound to the
    exact recorded head/base of the snapshot (the canonical ledger contract),
    while raw evidence preserves each review's original ``commit_id``.
    """
    dispositions = owner_dispositions or {}
    observations: list[dict[str, Any]] = []
    for item in evidence.get("review_comments", []):
        if not isinstance(item, dict):
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        comment_id = item.get("id")
        observation = _neutral_observation(
            pr_number,
            head,
            base,
            event_id=f"review-comment-{comment_id}",
            reviewer=str(user.get("login") or "github-review"),
            message=str(item.get("body") or "review finding"),
            path=item.get("path"),
            line=item.get("line") or item.get("original_line"),
        )
        if isinstance(comment_id, int) and comment_id in dispositions:
            observation["classification"] = dispositions[comment_id]
        observations.append(observation)
    for item in evidence.get("reviews", []):
        if not isinstance(item, dict):
            continue
        body = str(item.get("body") or "").strip()
        if not body:
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        observations.append(
            _neutral_observation(
                pr_number,
                head,
                base,
                event_id=f"review-{item.get('id')}",
                reviewer=str(user.get("login") or "github-review"),
                message=body,
                path=None,
                line=None,
            )
        )
    return observations


def owner_reply_dispositions(
    review_comments: list[Any], owner_login: str, *, reviewer_comment_ids: set[int]
) -> dict[int, str]:
    """Map original review-comment ids to an explicit owner disposition.

    A reply comment is an authoritative disposition only when the reply author
    is the repository owner and the reply body matches a deterministic
    false-positive or style/non-defect marker. Neither the original commenter
    nor a third party can dispose their own or someone else's finding.
    """
    dispositions: dict[int, str] = {}
    if not owner_login:
        return dispositions
    for item in review_comments:
        if not isinstance(item, dict):
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        if str(user.get("login") or "") != owner_login:
            continue
        target = item.get("in_reply_to_id")
        if not isinstance(target, int) or target not in reviewer_comment_ids:
            continue
        body = str(item.get("body") or "").casefold()
        if any(marker in body for marker in _FALSE_POSITIVE_MARKERS):
            dispositions[target] = CLASS_FALSE_POSITIVE
        elif any(marker in body for marker in _STYLE_OR_NON_DEFECT_MARKERS):
            dispositions[target] = CLASS_STYLE
    return dispositions


# ---------------------------------------------------------------------------
# Preloaded governed historical knowledge (existing backfill compatibility)
# ---------------------------------------------------------------------------


def _registry_and_family_maps(registry_path: Path) -> dict[str, dict[str, Any]]:
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    families = raw.get("families") if isinstance(raw, dict) else None
    if not isinstance(families, list):
        raise FullHistoryScanError("canonical registry families must be a list")
    result: dict[str, dict[str, Any]] = {}
    for family in families:
        if isinstance(family, dict) and isinstance(family.get("id"), str):
            result[family["id"]] = family
    return result


def preload_historical_observations(
    pr_number: int, head: str, base: str, backfill_path: Path, registry_path: Path
) -> list[dict[str, Any]]:
    """Translate owner-validated HBF records for *pr_number* into observations.

    This mirrors the canonical translation performed by
    ``incremental_knowledge_learning.historical_events`` (the single governed
    historical translation surface; event-ingestion design rule 10) so existing
    backfill assertions are neither redefined, duplicated, nor lost.

    ``confirmed`` HBF records classify as confirmed defects and carry their
    validated DFF plus the canonical family invariant/fix/regression evidence so
    KnowledgeExtractionAuthority can deterministically map them to existing
    families. Every non-confirmed record (style-non-defect/obsolete/false-
    positive) is translated exactly like the canonical surface: as a
    non-defect observation.

    A dedicated compatibility test asserts this function's output is identical
    to ``historical_events`` for the canonical backfill file.
    """
    raw = json.loads(backfill_path.read_text(encoding="utf-8"))
    records = raw.get("records")
    if not isinstance(records, list):
        raise FullHistoryScanError("historical backfill records must be a list")
    families = _registry_and_family_maps(registry_path)
    events: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise FullHistoryScanError("historical backfill record must be an object")
        if record.get("source_pr") != pr_number:
            continue
        classification = record.get("classification")
        assert_confirmed = classification == CLASS_CONFIRMED
        dff = record.get("dff_id")
        family = families.get(dff) if isinstance(dff, str) else None
        events.append(
            {
                "source": "github-review",
                "provider": "github-review",
                "event_id": str(record.get("id")),
                "source_pr": record.get("source_pr"),
                "reviewed_head_sha": head,
                "reviewed_base_sha": base,
                "reviewer": str(record.get("reviewer") or "historical-review"),
                "path": None,
                "line": None,
                "message": str(record.get("original_defect") or record.get("source_reference") or "historical finding"),
                "availability": "available",
                "classification": CLASS_CONFIRMED if assert_confirmed else CLASS_FALSE_POSITIVE,
                "invariant": _family_invariant(family) if assert_confirmed else "",
                "affected_paths": _family_paths(family) if assert_confirmed else [],
                "fix_reference": str(record.get("fix_reference") or ""),
                "regression_evidence": (
                    [record["regression_test_reference"]]
                    if assert_confirmed and record.get("regression_test_reference")
                    else []
                ),
                "claimed_family_id": (
                    dff if assert_confirmed and isinstance(dff, str) and dff.startswith("DFF-") else None
                ),
            }
        )
    return events


def _family_invariant(family: dict[str, Any] | None) -> str:
    if not family:
        return ""
    invariant = family.get("invariant")
    return str(invariant) if invariant else ""


def _family_paths(family: dict[str, Any] | None) -> list[str]:
    if not family:
        return []
    applicability = family.get("applicability")
    if not isinstance(applicability, dict):
        return []
    selectors = applicability.get("changed_paths")
    if not isinstance(selectors, list):
        return []
    paths: list[str] = []
    for selector in selectors:
        if not isinstance(selector, str) or not selector:
            continue
        paths.append(selector.rstrip("/") + "/__historical_evidence__" if selector.endswith("/") else selector)
    return paths


# ---------------------------------------------------------------------------
# Governed classification decisions (owner-adjudicated input)
# ---------------------------------------------------------------------------


def apply_governed_classifications(
    observations: list[dict[str, Any]], decisions: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Apply explicit owner-adjudicated classification decisions to observations.

    ``decisions`` is the machine-readable ``classification_decisions.json``
    artifact keyed by observation ``event_id``; each value optionally carries
    ``classification`` (canonical), ``claimed_family_id``, ``fix_reference`` and
    ``regression_evidence``. This is the only channel that can turn an otherwise
    neutral historical observation into a confirmed defect, and it never runs
    automatically. Unknown event ids and unsupported classifications fail closed.
    """
    if not decisions or not isinstance(decisions, dict):
        return observations
    result: list[dict[str, Any]] = []
    for observation in observations:
        decision = decisions.get(observation["event_id"])
        if decision is None:
            result.append(observation)
            continue
        if not isinstance(decision, dict):
            raise FullHistoryScanError(f"classification decision for {observation['event_id']} must be an object")
        classification = decision.get("classification")
        if classification not in EVENT_CLASSIFICATIONS:
            raise FullHistoryScanError(f"classification decision for {observation['event_id']} is not canonical")
        updated = dict(observation)
        updated["classification"] = classification
        if classification == CLASS_CONFIRMED:
            invariant = decision.get("invariant")
            if not isinstance(invariant, str) or not invariant.strip():
                raise FullHistoryScanError(f"confirmed decision for {observation['event_id']} requires an invariant")
            affected_paths = decision.get("affected_paths")
            if not isinstance(affected_paths, list) or not all(
                isinstance(item, str) and item.strip() for item in affected_paths
            ):
                raise FullHistoryScanError(f"confirmed decision for {observation['event_id']} requires affected_paths")
            fix_reference = decision.get("fix_reference")
            if not isinstance(fix_reference, str) or not fix_reference.strip():
                raise FullHistoryScanError(f"confirmed decision for {observation['event_id']} requires a fix_reference")
            regression_evidence = decision.get("regression_evidence")
            if not isinstance(regression_evidence, list) or not regression_evidence:
                raise FullHistoryScanError(
                    f"confirmed decision for {observation['event_id']} requires regression_evidence"
                )
            updated["invariant"] = invariant
            updated["affected_paths"] = affected_paths
            updated["fix_reference"] = fix_reference
            updated["regression_evidence"] = regression_evidence
            updated["claimed_family_id"] = decision.get("claimed_family_id")
        result.append(updated)
    return result


# ---------------------------------------------------------------------------
# Per-PR processing pipeline (extraction -> neutral observations -> ledger)
# ---------------------------------------------------------------------------


def build_pr_ledger(
    pr_number: int,
    head: str,
    base: str,
    observations: list[dict[str, Any]],
    registry_path: Path,
) -> dict[str, Any]:
    """Build the canonical hunter-learning-ledger-v1 artifact for one PR."""
    return build_learning_ledger(pr_number, head, base, observations, registry_path)


# ---------------------------------------------------------------------------
# Status derivation (Section 5: every PR ends in exactly one auditable status)
# ---------------------------------------------------------------------------


def derive_pr_status(record: dict[str, Any]) -> tuple[str | None, str | None]:
    """Derive the single explicit PR status from scan state and ledger outcomes."""
    scan_state = record.get("scan_state")
    if scan_state == SCAN_INFRA_RETRYABLE:
        return None, record.get("scan_reason")
    if scan_state == SCAN_INFRA_PERMANENT:
        return PR_STATUS_INFRA_PROVIDER_FAILURE, record.get("scan_reason")
    if scan_state == SCAN_PERMANENT_ERROR:
        return PR_STATUS_UNRESOLVED, record.get("scan_reason")
    if scan_state != SCAN_COMPLETE:
        return PR_STATUS_UNRESOLVED, f"unknown scan_state {scan_state!r}"

    ledger = record.get("ledger")
    items = ledger.get("items") if isinstance(ledger, dict) else None
    if not isinstance(items, list):
        return PR_STATUS_UNRESOLVED, "learning ledger is missing or malformed"

    has_pending = False
    has_admission_failure = False
    has_ambiguous = False
    has_candidate = False
    has_existing = False
    has_infra = False
    classifications: list[Any] = []
    for item in items:
        observation = item.get("observation") if isinstance(item, dict) else None
        classification = observation.get("classification") if isinstance(observation, dict) else None
        if isinstance(classification, str):
            classifications.append(classification)
        state = item.get("state") if isinstance(item, dict) else None
        if state == "insufficient-evidence":
            if classification is None:
                has_pending = True
            else:
                has_admission_failure = True
        elif state == "excluded":
            if classification in {CLASS_INFRASTRUCTURE, CLASS_PROVIDER_UNAVAILABLE}:
                has_infra = True
        elif state == "ambiguous":
            has_ambiguous = True
        elif state == "candidate-new-family":
            has_candidate = True
        elif state == "existing-family":
            has_existing = True

    if has_ambiguous:
        return PR_STATUS_UNRESOLVED, "confirmed finding claim could not map to canonical knowledge"
    if has_admission_failure:
        return PR_STATUS_UNRESOLVED, "a classified observation failed canonical event admission"
    if has_candidate:
        return PR_STATUS_CONFIRMED_DEFECT, "new confirmed defect proposal awaits governed family creation"
    if has_existing:
        return PR_STATUS_DUPLICATE_DEFECT, "confirmed findings map to existing canonical families"
    if CLASS_FALSE_POSITIVE in classifications:
        return PR_STATUS_FALSE_POSITIVE, "reviewer findings dispositioned false positive by owner"
    if any(classification in {CLASS_STYLE, CLASS_OBSOLETE} for classification in classifications):
        return PR_STATUS_STYLE_OR_NON_DEFECT, "reviewer findings are style/non-defect only"
    if has_infra:
        return PR_STATUS_INFRA_PROVIDER_FAILURE, "observations are infrastructure/provider-only"
    if has_pending:
        return PR_STATUS_FINDING_EXTRACTED, "neutral observations await governed validation/classification"
    return PR_STATUS_SCANNED_NO_FINDING, None


def _record_stable_body(record: dict[str, Any]) -> dict[str, Any]:
    stable_keys = (
        "pr_number",
        "head_sha",
        "base_sha",
        "evidence",
        "observations",
        "ledger",
        "scan_state",
    )
    return {key: record.get(key) for key in stable_keys}


def process_pr(
    request: RestRequest,
    repository: str,
    token: str,
    summary: dict[str, Any],
    *,
    registry_path: Path,
    backfill_path: Path,
    snapshot_captured_at: str,
    owner_login: str,
    collect_statuses: bool,
    max_pages: int = DEFAULT_MAX_PAGES,
    governed_classifications: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one PR through extraction -> neutral observations -> canonical ledger.

    Never grants canonical authority; it only emits immutable evidence, a
    canonical learning ledger, and a derived auditable status.
    """
    pr_number = summary["number"]
    head = summary.get("head_sha") or ""
    base = summary.get("base_sha") or ""
    if not _SHA_RE.fullmatch(head) or not _SHA_RE.fullmatch(base):
        raise ScanPermanentError(
            f"PR #{pr_number} head/base SHA is unavailable; cannot bind observations to an exact head"
        )

    evidence = collect_pr_evidence(
        request, repository, token, pr_number, collect_statuses=collect_statuses, max_pages=max_pages
    )

    reviewer_comment_ids = {
        int(item["id"])
        for item in evidence.get("review_comments", [])
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    }
    dispositions = owner_reply_dispositions(
        evidence.get("review_comments", []), owner_login, reviewer_comment_ids=reviewer_comment_ids
    )

    observations = extract_observations(pr_number, head, base, evidence, owner_dispositions=dispositions)
    preloaded = preload_historical_observations(pr_number, head, base, backfill_path, registry_path)
    # Owner-adjudicated decisions apply only to newly extracted evidence; the
    # canonical HBF translation is never redecided by a later decision file.
    collected = apply_governed_classifications(observations, governed_classifications)
    observations = preloaded + collected

    ledger = build_pr_ledger(pr_number, head, base, observations, registry_path)

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "pr_number": pr_number,
        "title": summary.get("title"),
        "state": summary.get("state"),
        "merged": summary.get("merged"),
        "is_draft": summary.get("is_draft"),
        "author": summary.get("author"),
        "head_sha": head,
        "base_sha": base,
        "snapshot_captured_at": snapshot_captured_at,
        "evidence": evidence,
        "observations": observations,
        "ledger": ledger,
        "proposal_outcomes": _ledger_outcome_counts(ledger),
        "scan_state": SCAN_COMPLETE,
        "scan_reason": None,
        "attempts": 1,
        "processed_at": utc_now_iso(),
        "explicit_classification_decisions": bool(governed_classifications),
    }
    status, reason = derive_pr_status(record)
    record["status"] = status
    record["status_reason"] = reason
    record["record_digest"] = stable_json_digest(_record_stable_body(record))
    return record


def _ledger_outcome_counts(ledger: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    items = ledger.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                state = str(item.get("state") or "unknown")
                counts[state] = counts.get(state, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Checkpoints, resume, and progress
# ---------------------------------------------------------------------------


def checkpoint_path(data_dir: Path, batch_index: int) -> Path:
    return data_dir / "checkpoints" / f"checkpoint-{batch_index:04d}.json"


def progress_path(data_dir: Path) -> Path:
    return data_dir / "progress.json"


def read_progress(data_dir: Path) -> dict[str, Any] | None:
    return read_json_file(progress_path(data_dir))


def write_checkpoint(data_dir: Path, batch_index: int, batch: list[int], pr_records: list[dict[str, Any]]) -> Path:
    body = {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "batch_index": batch_index,
        "batch_pr_numbers": batch,
        "pr_successes": [record["pr_number"] for record in pr_records if record.get("status") is not None],
        "pr_statuses": {
            str(record["pr_number"]): {
                "status": record.get("status"),
                "digest": record.get("record_digest"),
            }
            for record in pr_records
        },
        "written_at_utc": utc_now_iso(),
    }
    path = checkpoint_path(data_dir, batch_index)
    atomic_write_json(path, body)
    return path


def write_progress(
    data_dir: Path,
    *,
    repository: str,
    snapshot_id: str,
    snapshot_total: int,
    batch_size: int,
    next_batch: int,
    completed_prs: list[int],
    pending_prs: list[int],
) -> Path:
    body = {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "repository": repository,
        "snapshot_id": snapshot_id,
        "snapshot_total": snapshot_total,
        "batch_size": batch_size,
        "next_batch": next_batch,
        "completed_prs": sorted(set(completed_prs)),
        "pending_prs": sorted(set(pending_prs)),
        "updated_at_utc": utc_now_iso(),
    }
    path = progress_path(data_dir)
    atomic_write_json(path, body)
    return path


def load_record(data_dir: Path, pr_number: int) -> dict[str, Any] | None:
    return read_json_file(data_dir / "prs" / f"{pr_number}.json")


def store_record(data_dir: Path, record: dict[str, Any]) -> Path:
    path = data_dir / "prs" / f"{record['pr_number']}.json"
    atomic_write_json(path, record)
    return path


# ---------------------------------------------------------------------------
# Batch execution loop (checkpoint per batch, resume, idempotent replay)
# ---------------------------------------------------------------------------


def run_batches(
    request: RestRequest,
    repository: str,
    token: str,
    *,
    data_dir: Path,
    snapshot: dict[str, Any],
    batches: list[list[int]],
    registry_path: Path,
    backfill_path: Path,
    owner_login: str,
    collect_statuses: bool,
    max_pages: int,
    max_attempts: int,
    resume: bool,
    out_of_scope: dict[int, str],
    governed_classifications: dict[str, Any] | None,
    summaries_by_number: dict[int, dict[str, Any]],
    progress: Any | None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Process batched snapshot PRs with checkpointing/resume, returning results.

    Cells evolved:
    - checkpoint written after every completed batch;
    - interruption survives re-entry (--resume skips completed PRs);
    - idempotent replay (identical digest skip);
    - infra/provider failures are bounded-retried across runs and only become
      the explicit INFRA_PROVIDER_FAILURE status after max_attempts;
    - provider/API failure never becomes a product defect.
    """
    completed: list[int] = []
    processed: list[int] = []
    findings_extracted_total = 0
    status_counts: dict[str, int] = {status: 0 for status in PR_STATUSES}
    infra_retryable: list[int] = []

    if progress is not None:
        completed = list(progress.get("completed_prs") or [])
        processed = list(completed)

    next_batch = 1
    total_batches = len(batches)
    batch_records: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(batches, start=1):
        if resume:
            # Resume granularity is per-PR, not per-batch: a batch whose only
            # outstanding PR is still infra-retryable must be re-entered so its
            # bounded attempt budget can advance to exhaustion.
            todo = [number for number in batch if number not in completed]
            if not todo:
                next_batch = batch_index + 1
                continue
        else:
            todo = list(batch)

        for number in todo:
            summary = summaries_by_number.get(number)
            if summary is None:
                raise FullHistoryScanError(f"PR #{number} missing from snapshot summaries")
            if number in out_of_scope:
                record = _out_of_scope_record(
                    repository, summary, out_of_scope[number], snapshot.get("captured_at_utc")
                )
                store_record(data_dir, record)
                processed.append(number)
                status_counts[PR_STATUS_OUT_OF_SCOPE] = status_counts.get(PR_STATUS_OUT_OF_SCOPE, 0) + 1
                continue
            record = _process_with_retry_handling(
                request,
                repository,
                token,
                summary,
                data_dir=data_dir,
                registry_path=registry_path,
                backfill_path=backfill_path,
                snapshot_captured_at=snapshot.get("captured_at_utc"),
                owner_login=owner_login,
                collect_statuses=collect_statuses,
                max_pages=max_pages,
                max_attempts=max_attempts,
                governed_classifications=governed_classifications,
            )
            status = record.get("status")
            if status in PR_STATUSES:
                status_counts[status] = status_counts.get(status, 0) + 1
            scan_state = record.get("scan_state")
            if scan_state == SCAN_INFRA_RETRYABLE:
                infra_retryable.append(number)
            if scan_state != SCAN_INFRA_RETRYABLE:
                processed.append(number)
            if status == PR_STATUS_FINDING_EXTRACTED:
                findings_extracted_total += 1
            batch_records.append(record)

        next_batch = batch_index + 1
        completed = sorted(set(processed))
        pending_prs = sorted(set(summaries_by_number) - set(completed))
        write_progress(
            data_dir,
            repository=repository,
            snapshot_id=snapshot.get("snapshot_id"),
            snapshot_total=snapshot.get("total_prs"),
            batch_size=batches[0] and len(batches[0]) or DEFAULT_BATCH_SIZE,
            next_batch=next_batch,
            completed_prs=completed,
            pending_prs=pending_prs,
        )
        checkpoint = write_checkpoint(data_dir, batch_index, batch, batch_records)
        print_progress(
            snapshot_total=snapshot.get("total_prs"),
            batch_index=batch_index,
            total_batches=total_batches,
            processed=len(completed),
            findings_extracted=findings_extracted_total,
            status_counts=status_counts,
            checkpoint=checkpoint,
            infra_retryable=infra_retryable,
        )
        if dry_run:
            break

    result = {
        "completed_prs": sorted(set(processed)),
        "status_counts": status_counts,
        "findings_extracted_total": findings_extracted_total,
        "next_batch": next_batch,
        "batches_total": total_batches,
        "batches_completed": next_batch - 1,
        "infra_retryable": infra_retryable,
        "last_checkpoint": f"checkpoint-{next_batch - 1:04d}.json" if next_batch > 1 else None,
    }
    return result


def _out_of_scope_record(repository: str, summary: dict[str, Any], reason: str, captured_at: str) -> dict[str, Any]:
    number = summary["number"]
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "pr_number": number,
        "title": summary.get("title"),
        "state": summary.get("state"),
        "merged": summary.get("merged"),
        "is_draft": summary.get("is_draft"),
        "author": summary.get("author"),
        "head_sha": summary.get("head_sha"),
        "base_sha": summary.get("base_sha"),
        "snapshot_captured_at": captured_at,
        "evidence": {},
        "observations": [],
        "ledger": {"schema_version": "hunter-learning-ledger-v1", "items": []},
        "proposal_outcomes": {},
        "scan_state": SCAN_COMPLETE,
        "scan_reason": None,
        "attempts": 1,
        "processed_at": utc_now_iso(),
        "status": PR_STATUS_OUT_OF_SCOPE,
        "status_reason": reason,
        "record_digest": stable_json_digest(
            {
                "pr_number": number,
                "head_sha": summary.get("head_sha"),
                "base_sha": summary.get("base_sha"),
                "status": PR_STATUS_OUT_OF_SCOPE,
                "reason": reason,
            }
        ),
    }


def _process_with_retry_handling(
    request: RestRequest,
    repository: str,
    token: str,
    summary: dict[str, Any],
    *,
    data_dir: Path,
    registry_path: Path,
    backfill_path: Path,
    snapshot_captured_at: str,
    owner_login: str,
    collect_statuses: bool,
    max_pages: int,
    max_attempts: int,
    governed_classifications: dict[str, Any] | None,
) -> dict[str, Any]:
    number = summary["number"]
    prior = load_record(data_dir, number)
    attempts = int(prior.get("attempts") or 0) if prior else 0
    attempts = attempts + 1
    try:
        record = process_pr(
            request,
            repository,
            token,
            summary,
            registry_path=registry_path,
            backfill_path=backfill_path,
            snapshot_captured_at=snapshot_captured_at,
            owner_login=owner_login,
            collect_statuses=collect_statuses,
            max_pages=max_pages,
            governed_classifications=governed_classifications,
        )
    except ScanInfrastructureUnavailable as exc:
        if attempts >= max_attempts:
            status = PR_STATUS_INFRA_PROVIDER_FAILURE
            scan_state = SCAN_INFRA_PERMANENT
            reason = f"GitHub/provider infrastructure unavailable after {attempts} attempts: {exc}"
        else:
            scan_state = SCAN_INFRA_RETRYABLE
            status = None
            reason = str(exc)
        return _failure_record(data_dir, repository, summary, attempts, scan_state, status, reason)
    except ScanPermanentError as exc:
        return _failure_record(
            data_dir, repository, summary, attempts, SCAN_PERMANENT_ERROR, PR_STATUS_UNRESOLVED, str(exc)
        )
    record["attempts"] = attempts
    store_record(data_dir, record)
    return record


def _failure_record(
    data_dir: Path,
    repository: str,
    summary: dict[str, Any],
    attempts: int,
    scan_state: str,
    status: str | None,
    reason: str,
) -> dict[str, Any]:
    number = summary["number"]
    record = {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "pr_number": number,
        "title": summary.get("title"),
        "state": summary.get("state"),
        "merged": summary.get("merged"),
        "is_draft": summary.get("is_draft"),
        "author": summary.get("author"),
        "head_sha": summary.get("head_sha"),
        "base_sha": summary.get("base_sha"),
        "evidence": {},
        "observations": [],
        "ledger": None,
        "proposal_outcomes": {},
        "scan_state": scan_state,
        "scan_reason": reason,
        "attempts": attempts,
        "processed_at": utc_now_iso(),
        "status": status,
        "status_reason": reason,
        "record_digest": stable_json_digest(
            {"pr_number": number, "summary": summary, "scan_state": scan_state, "reason": reason}
        ),
    }
    store_record(data_dir, record)
    return record


def print_progress(
    *,
    snapshot_total: int,
    batch_index: int,
    total_batches: int,
    processed: int,
    findings_extracted: int,
    status_counts: dict[str, int],
    checkpoint: Path,
    infra_retryable: list[int],
) -> None:
    coverage = (processed / snapshot_total * 100.0) if snapshot_total else 100.0
    print(
        f"snapshot: {snapshot_total} PRs\n"
        f"batch: {batch_index}/{total_batches}\n"
        f"PRs processed: {processed}/{snapshot_total}\n"
        f"findings extracted: {findings_extracted}\n"
        f"confirmed: {status_counts.get(PR_STATUS_CONFIRMED_DEFECT, 0)}\n"
        f"duplicate: {status_counts.get(PR_STATUS_DUPLICATE_DEFECT, 0)}\n"
        f"excluded infra/provider: {status_counts.get(PR_STATUS_INFRA_PROVIDER_FAILURE, 0)}\n"
        f"unresolved: {status_counts.get(PR_STATUS_UNRESOLVED, 0)}\n"
        f"coverage: {coverage:.2f}%\n"
        f"checkpoint: {checkpoint}\n"
        f"infra retryable: {infra_retryable}\n"
    )


# ---------------------------------------------------------------------------
# Coverage manifest (Section 15) and gap accounting
# ---------------------------------------------------------------------------


def build_coverage_manifest(
    *,
    repository: str,
    initial_snapshot: dict[str, Any],
    final_snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    batches_completed: int,
    batches_total: int,
    last_checkpoint: str,
) -> dict[str, Any]:
    """Machine-readable final coverage manifest; hard completion iff gap == 0."""
    final_numbers = sorted(summary["number"] for summary in final_snapshot["prs"])
    initial_numbers = sorted(summary["number"] for summary in initial_snapshot["prs"])
    record_by_number = {record["pr_number"]: record for record in records}

    status_counts: dict[str, int] = {status: 0 for status in sorted(PR_STATUSES)}
    terminal_statused: list[int] = []
    missing: list[int] = []
    for number in final_numbers:
        record = record_by_number.get(number)
        if record is None:
            missing.append(number)
            continue
        status = record.get("status")
        if status in PR_STATUSES:
            status_counts[status] += 1
            terminal_statused.append(number)
        else:
            missing.append(number)

    existing_family_mappings: dict[str, list[int]] = {}
    new_family_proposals: list[dict[str, Any]] = []
    for number in final_numbers:
        record = record_by_number.get(number)
        if record is None:
            continue
        ledger = record.get("ledger") if isinstance(record, dict) else None
        items = ledger.get("items") if isinstance(ledger, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("state") == "existing-family":
                family_id = None
                proposal = item.get("proposal")
                if isinstance(proposal, dict):
                    family_id = proposal.get("canonical_family_id")
                if isinstance(family_id, str):
                    existing_family_mappings.setdefault(family_id, [])
                    if number not in existing_family_mappings[family_id]:
                        existing_family_mappings[family_id].append(number)
            elif item.get("state") == "candidate-new-family":
                observation = item.get("observation")
                new_family_proposals.append(
                    {
                        "pr_number": number,
                        "event_id": observation.get("event_id") if isinstance(observation, dict) else None,
                        "awaiting": "governed family creation",
                    }
                )

    coverage_gap = len(final_numbers) - len(terminal_statused)
    complete = coverage_gap == 0
    manifest = {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "repository": repository,
        "initial_snapshot": {
            "id": initial_snapshot.get("snapshot_id"),
            "captured_at": initial_snapshot.get("captured_at_utc"),
            "total_prs": initial_snapshot.get("total_prs"),
        },
        "final_snapshot": {
            "id": final_snapshot.get("snapshot_id"),
            "captured_at": final_snapshot.get("captured_at_utc"),
            "total_prs": final_snapshot.get("total_prs"),
        },
        "total_prs_in_final_scope": len(final_numbers),
        "total_statused": len(terminal_statused),
        "status_counts": status_counts,
        "no_finding_count": status_counts[PR_STATUS_SCANNED_NO_FINDING],
        "extracted_finding_count": status_counts[PR_STATUS_FINDING_EXTRACTED],
        "confirmed_defect_count": status_counts[PR_STATUS_CONFIRMED_DEFECT],
        "duplicate_count": status_counts[PR_STATUS_DUPLICATE_DEFECT],
        "false_positive_count": status_counts[PR_STATUS_FALSE_POSITIVE],
        "style_non_defect_count": status_counts[PR_STATUS_STYLE_OR_NON_DEFECT],
        "out_of_scope_count": status_counts[PR_STATUS_OUT_OF_SCOPE],
        "infra_provider_exclusion_count": status_counts[PR_STATUS_INFRA_PROVIDER_FAILURE],
        "unresolved_count": status_counts[PR_STATUS_UNRESOLVED],
        "existing_family_mappings": existing_family_mappings,
        "new_family_proposals": new_family_proposals,
        "catch_up_pr_count": len(sorted(set(final_numbers) - set(initial_numbers))),
        "missing_status_records": missing,
        "checkpoint_summary": {
            "batches_total": batches_total,
            "batches_completed": batches_completed,
            "last_checkpoint": last_checkpoint,
        },
        "coverage_gap": coverage_gap,
        "complete": complete,
    }
    return manifest
