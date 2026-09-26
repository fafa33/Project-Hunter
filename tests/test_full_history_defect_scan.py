"""Tests for the full-history defect backfill worker (mission section 7).

Each required behavior family from the mission is covered:

1.  determinism + complete snapshot (open/closed/merged/draft)
2.  batching with a checkpoint after every completed batch
3.  resume after interruption (no unnecessary reprocessing)
4.  idempotent replay (identical record digest)
5.  duplicate avoidance for existing HBF knowledge
6.  429 bounded retry then explicit INFRA_PROVIDER_FAILURE
7.  5xx bounded retry then explicit INFRA_PROVIDER_FAILURE
8.  multiple 4xx never retried blindly (permanent scan error, UNRESOLVED)
9.  provider failure exclusion (never a product defect)
10. rate-limit/quota exclusion
11. owner false-positive disposition
12. unresolved/neutral evidence handling (FINDING_EXTRACTED)
13. provenance preservation of raw evidence
14. existing HBF compatibility (=== canonical historical_events)
15. coverage-accounting manifest
16. catch-up pass gap closure
17. coverage-gap detection
18. explicit status for every PR
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

import hunter_full_history_defect_scan as cli
import pytest

from hunter.evidence_intelligence import full_history_defect_scan as scanner
from hunter.evidence_intelligence.incremental_knowledge_learning import historical_events

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_BACKFILL = ROOT / "docs" / "HISTORICAL_DEFECT_BACKFILL.json"
CANONICAL_REGISTRY = ROOT / "docs" / "DEFECT_REGISTRY.json"

REPO = "fake/scan"


def sha(token: str) -> str:
    """Deterministic 40-char lowercase hex SHA (canonical exact-head contract)."""
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:40]


def make_pr_summary(number: int, *, state: str = "closed", merged: bool = True) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"PR #{number}",
        "state": state,
        "is_draft": False,
        "merged": merged,
        "merged_at": "2024-01-01T00:00:00Z" if merged else None,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "closed_at": "2024-01-02T00:00:00Z" if state == "closed" else None,
        "author": "author-1",
        "head_sha": sha(f"head-{number}"),
        "head_ref": f"feat/{number}",
        "base_sha": sha(f"base-{number}"),
        "base_ref": "main",
        "merge_commit_sha": sha(f"merge-{number}"),
    }


class FakeTransport:
    """Deterministic fake GitHub REST surface for the scanner.

    ``paginated`` maps an endpoint key to a list of pages (each page a list).
    ``single`` maps an endpoint key to a single object. ``failures`` maps an
    endpoint key to a FIFO of exceptions to raise instead of the payload.
    """

    def __init__(self) -> None:
        self.paginated: dict[str, list[list[dict[str, Any]]]] = {}
        self.single: dict[str, Any] = {}
        self.failures: dict[str, list[BaseException]] = {}
        self.calls: list[tuple[str, str]] = []

    def route_pages(self, key: str, pages: list[list[dict[str, Any]]]) -> FakeTransport:
        self.paginated[key] = pages
        return self

    def route_single(self, key: str, payload: Any) -> FakeTransport:
        self.single[key] = payload
        return self

    def fail(self, key: str, *exceptions: BaseException) -> FakeTransport:
        self.failures[key] = list(exceptions)
        return self

    def count(self, key: str) -> int:
        return sum(1 for stored, _ in self.calls if stored == key)

    def __call__(self, repository: str, token: str, method: str, path: str) -> Any:
        key, _, query = path.partition("?")
        params = urllib.parse.parse_qs(query)
        page = int(params["page"][0]) if "page" in params else 1
        self.calls.append((key, method))
        pending = self.failures.get(key)
        if pending:
            raise pending.pop(0)
        if key in self.paginated:
            pages = self.paginated[key]
            return pages[min(page - 1, len(pages) - 1)]
        if key in self.single:
            return self.single[key]
        raise KeyError(f"no fake route for {path!r}")


def make_registry(tmp_path: Path, *family_ids: str) -> Path:
    path = tmp_path / "DEFECT_REGISTRY.json"
    families = []
    for i, family_id in enumerate(family_ids, start=1):
        families.append(
            {
                "id": family_id,
                "name": family_id,
                "invariant": f"invariant-{i}",
                "lifecycle": "recorded",
                "applicability": {"changed_paths": [f"src/components/{i}/"]},
                "prevention": {"boundary": "review"},
            }
        )
    path.write_text(json.dumps({"families": families}, indent=2), encoding="utf-8")
    return path


def make_backfill(tmp_path: Path) -> Path:
    path = tmp_path / "HISTORICAL_DEFECT_BACKFILL.json"
    path.write_text(json.dumps({"records": []}, indent=2), encoding="utf-8")
    return path


def empty_transport() -> FakeTransport:
    """A transport whose PRs (1..6) have no comments/reviews/files/commits at all."""
    transport = FakeTransport()
    for number in range(1, 7):
        route_empty_pr(transport, number)
    return transport


def route_empty_pr(transport: FakeTransport, number: int) -> None:
    summary = make_pr_summary(number)
    transport.route_single(
        f"pulls/{number}",
        {
            "number": number,
            "state": "closed",
            "merged_at": "2024-01-01T00:00:00Z",
            "head": {"sha": summary["head_sha"]},
            "base": {"sha": summary["base_sha"]},
        },
    )
    transport.route_pages(f"pulls/{number}/commits", [[]])
    transport.route_pages(f"pulls/{number}/comments", [[]])
    transport.route_pages(f"pulls/{number}/reviews", [[]])
    transport.route_pages(f"pulls/{number}/files", [[]])
    transport.route_pages(f"issues/{number}/comments", [[]])


def run_scan(
    tmp_path: Path,
    summaries: list[dict[str, Any]],
    transport: FakeTransport,
    *,
    registry: Path | None = None,
    backfill: Path | None = None,
    batch_size: int = 2,
    max_pages: int = 5,
    max_attempts: int = 3,
    resume: bool = False,
    progress: dict[str, Any] | None = None,
    out_of_scope: dict[int, str] | None = None,
    classifications: dict[str, Any] | None = None,
    owner_login: str = "owner-1",
) -> dict[str, Any]:
    snapshot = {
        "schema_version": scanner.SNAPSHOT_SCHEMA_VERSION,
        "repository": REPO,
        "captured_at_utc": "2024-01-01T00:00:00Z",
        "total_prs": len(summaries),
        "snapshot_id": scanner.stable_json_digest({"repository": REPO, "numbers": [s["number"] for s in summaries]}),
        "prs": summaries,
    }
    if registry is None:
        registry = make_registry(tmp_path, "DFF-111")
    if backfill is None:
        backfill = make_backfill(tmp_path)
    return scanner.run_batches(
        transport,
        REPO,
        "tok",
        data_dir=tmp_path,
        snapshot=snapshot,
        batches=scanner.partition_batches([s["number"] for s in summaries], batch_size),
        registry_path=registry,
        backfill_path=backfill,
        owner_login=owner_login,
        collect_statuses=False,
        max_pages=max_pages,
        max_attempts=max_attempts,
        resume=resume,
        out_of_scope=out_of_scope or {},
        governed_classifications=classifications,
        summaries_by_number={s["number"]: s for s in summaries},
        progress=progress,
    )


def record_checked(tmp_path: Path, pr_number: int) -> dict[str, Any]:
    record = scanner.load_record(tmp_path, pr_number)
    assert record is not None, f"missing record for PR #{pr_number}"
    return record


def build_snapshot_file(tmp_path: Path, summaries: list[dict[str, Any]]) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": scanner.SNAPSHOT_SCHEMA_VERSION,
                "repository": REPO,
                "captured_at_utc": "2024-01-01T00:00:00Z",
                "total_prs": len(summaries),
                "snapshot_id": "sid",
                "prs": summaries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 1. Deterministic, complete snapshot
# ---------------------------------------------------------------------------


def test_capture_snapshot_is_deterministic_and_complete() -> None:
    merged_payload = {
        "number": 50,
        "user": {"login": "a"},
        "head": {"sha": sha("h50")},
        "base": {"sha": sha("b50")},
        "state": "closed",
        "merged_at": "2024-01-01T00:00:00Z",
        "title": "t",
    }
    draft_payload = {
        "number": 101,
        "user": {"login": "c"},
        "head": {"sha": sha("h101")},
        "base": {"sha": sha("b101")},
        "state": "open",
        "draft": True,
        "title": "t101",
    }
    page_one = [
        {
            "number": n,
            "user": {"login": "a"},
            "head": {"sha": sha(f"h{n}")},
            "base": {"sha": sha(f"b{n}")},
            "state": "closed",
            "title": f"t{n}",
        }
        for n in range(1, 101)
    ]
    page_one[49] = merged_payload
    page_two = [draft_payload]
    payload_pages = [page_one, page_two]

    def request(repository: str, token: str, method: str, path: str) -> Any:
        del repository, token, method
        assert path.startswith("pulls?state=all&per_page=100&page=")
        page = int(path.rsplit("page=", 1)[1])
        return payload_pages[min(page - 1, len(payload_pages) - 1)]

    first = scanner.capture_snapshot(request, REPO, "tok")
    second = scanner.capture_snapshot(request, REPO, "tok")
    assert first["snapshot_id"] == second["snapshot_id"]
    numbers = [summary["number"] for summary in first["prs"]]
    assert numbers == list(range(1, 102))
    assert len(numbers) == 101
    merged = next(summary for summary in first["prs"] if summary["number"] == 50)
    assert merged["merged"] is True
    draft = next(summary for summary in first["prs"] if summary["number"] == 101)
    assert draft["is_draft"] is True
    assert draft["state"] == "open"
    assert all("head_sha" in summary for summary in first["prs"])


def test_capture_snapshot_fails_closed_beyond_max_pages() -> None:
    five_full_pages = [[{"number": n, "state": "open", "title": "t"} for n in range(1, 101)] for _ in range(5)]

    def request(repository: str, token: str, method: str, path: str) -> Any:
        del repository, token, method
        page = int(path.rsplit("page=", 1)[1]) - 1
        return five_full_pages[min(page, 4)]

    with pytest.raises(scanner.FullHistoryScanError, match="max-pages"):
        scanner.capture_snapshot(request, REPO, "tok", max_pages=5)


# ---------------------------------------------------------------------------
# 2. Batching with per-batch checkpoints
# ---------------------------------------------------------------------------


def test_run_batches_checkpoints_each_batch(tmp_path: Path) -> None:
    summaries = [make_pr_summary(n) for n in (1, 2, 3, 4, 5)]
    transport = empty_transport()
    result = run_scan(tmp_path, summaries, transport, batch_size=2)
    assert result["batches_total"] == 3
    assert result["batches_completed"] == 3
    assert result["completed_prs"] == [1, 2, 3, 4, 5]
    assert (tmp_path / "checkpoints" / "checkpoint-0001.json").is_file()
    assert (tmp_path / "checkpoints" / "checkpoint-0002.json").is_file()
    assert (tmp_path / "checkpoints" / "checkpoint-0003.json").is_file()
    progress = scanner.read_progress(tmp_path)
    assert progress is not None
    assert progress["next_batch"] == 4
    assert sorted(progress["completed_prs"]) == [1, 2, 3, 4, 5]
    assert progress["pending_prs"] == []
    for number in (1, 2, 3, 4, 5):
        record = record_checked(tmp_path, number)
        assert record["status"] == scanner.PR_STATUS_SCANNED_NO_FINDING
        assert record["scan_state"] == scanner.SCAN_COMPLETE


# ---------------------------------------------------------------------------
# 3. Resume after interruption (no unnecessary reprocessing)
# ---------------------------------------------------------------------------


def test_resume_skips_completed_batches(tmp_path: Path) -> None:
    summaries = [make_pr_summary(n) for n in (1, 2, 3, 4)]
    transport = empty_transport()
    first = scanner.run_batches(
        transport,
        REPO,
        "tok",
        data_dir=tmp_path,
        snapshot={"prs": summaries, "total_prs": 4, "snapshot_id": "sid", "captured_at_utc": "now"},
        batches=scanner.partition_batches([1, 2], 2),
        registry_path=make_registry(tmp_path, "DFF-111"),
        backfill_path=make_backfill(tmp_path),
        owner_login="owner-1",
        collect_statuses=False,
        max_pages=5,
        max_attempts=3,
        resume=False,
        out_of_scope={},
        governed_classifications=None,
        summaries_by_number={s["number"]: s for s in summaries},
        progress=None,
    )
    assert first["completed_prs"] == [1, 2]
    processed_at_1 = record_checked(tmp_path, 1)["processed_at"]
    assert (tmp_path / "checkpoints" / "checkpoint-0001.json").is_file()
    assert not (tmp_path / "checkpoints" / "checkpoint-0002.json").is_file()

    progress = scanner.read_progress(tmp_path)
    assert progress is not None
    second = scanner.run_batches(
        transport,
        REPO,
        "tok",
        data_dir=tmp_path,
        snapshot={"prs": summaries, "total_prs": 4, "snapshot_id": "sid", "captured_at_utc": "now"},
        batches=scanner.partition_batches([1, 2, 3, 4], 2),
        registry_path=make_registry(tmp_path, "DFF-111"),
        backfill_path=make_backfill(tmp_path),
        owner_login="owner-1",
        collect_statuses=False,
        max_pages=5,
        max_attempts=3,
        resume=True,
        out_of_scope={},
        governed_classifications=None,
        summaries_by_number={s["number"]: s for s in summaries},
        progress=progress,
    )
    assert second["completed_prs"] == [1, 2, 3, 4]
    assert record_checked(tmp_path, 1)["processed_at"] == processed_at_1
    assert record_checked(tmp_path, 3)["status"] == scanner.PR_STATUS_SCANNED_NO_FINDING
    assert (tmp_path / "checkpoints" / "checkpoint-0002.json").is_file()


# ---------------------------------------------------------------------------
# 4. Idempotent replay (identical record digest)
# ---------------------------------------------------------------------------


def test_idempotent_replay_identical_digest(tmp_path: Path) -> None:
    summary = make_pr_summary(1)
    transport = empty_transport()
    record_a = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=make_registry(tmp_path, "DFF-111"),
        backfill_path=make_backfill(tmp_path),
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
    )
    process_b = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=make_registry(tmp_path, "DFF-111"),
        backfill_path=make_backfill(tmp_path),
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
    )
    assert record_a["record_digest"] == process_b["record_digest"]
    assert record_a["ledger"]["ledger_digest"] == process_b["ledger"]["ledger_digest"]


def test_reprocessing_existing_record_is_stable(tmp_path: Path) -> None:
    summaries = [make_pr_summary(1)]
    transport = empty_transport()
    run_scan(tmp_path, summaries, transport)
    digest = record_checked(tmp_path, 1)["record_digest"]
    transport_again = empty_transport()
    run_scan(tmp_path, summaries, transport_again)
    assert record_checked(tmp_path, 1)["record_digest"] == digest


# ---------------------------------------------------------------------------
# 5. Duplicate avoidance for existing HBF knowledge
# ---------------------------------------------------------------------------


def test_hbf_preload_maps_once_to_existing_family(tmp_path: Path) -> None:
    registry = make_registry(tmp_path, "DFF-111")
    backfill = tmp_path / "HISTORICAL_DEFECT_BACKFILL.json"
    backfill.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": "HBF-001-001",
                        "source_pr": 1,
                        "reviewer": "r",
                        "classification": "confirmed",
                        "original_defect": "the bug",
                        "source_reference": "ref",
                        "dff_id": "DFF-111",
                        "fix_reference": "fix",
                        "regression_test_reference": "regression",
                        "status": "guarded",
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    summary = make_pr_summary(1)
    transport = empty_transport()
    record = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=registry,
        backfill_path=backfill,
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
    )
    ledger = record["ledger"]
    states = [item["state"] for item in ledger["items"]]
    assert states == ["existing-family"]
    assert record["proposal_outcomes"]["existing-family"] == 1
    assert record["status"] == scanner.PR_STATUS_DUPLICATE_DEFECT
    assert ledger["source_availability"] == {"github-review": "available"}


# ---------------------------------------------------------------------------
# 6/7/9/10. Infra/provider failure exclusion: 429/5xx/quota bounded, never a defect
# ---------------------------------------------------------------------------


def _exclusion_cycle(tmp_path: Path, kind: str) -> dict[str, Any]:
    summaries = [make_pr_summary(1)]
    for _attempt in (1, 2, 3):
        transport = empty_transport()
        transport.fail(
            "pulls/1/commits",
            scanner.ScanInfrastructureUnavailable(f"{kind} after transport retries"),
        )
        progress = scanner.read_progress(tmp_path)
        run_scan(
            tmp_path,
            summaries,
            transport,
            max_attempts=3,
            resume=progress is not None,
            progress=progress,
        )
    record = scanner.load_record(tmp_path, 1)
    assert record is not None
    assert record["scan_state"] == scanner.SCAN_INFRA_PERMANENT
    assert record["status"] == scanner.PR_STATUS_INFRA_PROVIDER_FAILURE
    assert record["attempts"] == 3
    return record


def test_429_bounded_retry_then_infra_exclusion(tmp_path: Path) -> None:
    record = _exclusion_cycle(tmp_path, "429 too many requests")
    assert record["status_reason"].startswith("GitHub/provider infrastructure unavailable")
    assert "429" in record["status_reason"]


def test_503_bounded_retry_then_infra_exclusion(tmp_path: Path) -> None:
    _exclusion_cycle(tmp_path, "503 service unavailable")


def test_rate_limit_quota_exclusion(tmp_path: Path) -> None:
    _exclusion_cycle(tmp_path, "rate limit exhausted")


def test_infra_exclusion_never_a_defect(tmp_path: Path) -> None:
    record = _exclusion_cycle(tmp_path, "451 unavailable")
    assert record["ledger"] is None or record["ledger"]["items"] == []
    assert record["status"] != scanner.PR_STATUS_CONFIRMED_DEFECT
    assert record["status"] != scanner.PR_STATUS_DUPLICATE_DEFECT


# ---------------------------------------------------------------------------
# 8. Multiple 4xx: no blind retry, permanent scan error
# ---------------------------------------------------------------------------


def test_4xx_permanent_error_no_blind_retry(tmp_path: Path) -> None:
    summaries = [make_pr_summary(1)]
    transport = empty_transport()
    transport.fail("pulls/1/comments", scanner.ScanPermanentError("404 not found"))
    result = run_scan(tmp_path, summaries, transport)
    record = scanner.load_record(tmp_path, 1)
    assert record is not None
    assert record["scan_state"] == scanner.SCAN_PERMANENT_ERROR
    assert record["status"] == scanner.PR_STATUS_UNRESOLVED
    assert record["attempts"] == 1
    assert result["completed_prs"] == [1]
    assert record["scan_reason"].startswith("404")


# ---------------------------------------------------------------------------
# 11. Owner false-positive disposition
# ---------------------------------------------------------------------------


def test_owner_reply_false_positive_disposition(tmp_path: Path) -> None:
    summary = make_pr_summary(1)
    transport = empty_transport()
    review_comments = [
        {
            "id": 500,
            "user": {"login": "reviewer-1"},
            "body": "level check missing",
            "path": "src/module.py",
            "line": 3,
            "original_line": 3,
        },
        {
            "id": 501,
            "user": {"login": "reviewer-1"},
            "body": "level check missing",
            "path": "src/module.py",
            "line": 5,
            "original_line": 5,
        },
        {
            "id": 502,
            "user": {"login": "owner-1"},
            "body": "not a bug",
            "path": "src/module.py",
            "line": 505,
            "original_line": None,
            "in_reply_to_id": 501,
        },
    ]
    transport.route_pages("pulls/1/comments", [review_comments])
    record = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=make_registry(tmp_path, "DFF-111"),
        backfill_path=make_backfill(tmp_path),
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
    )
    observations = {obs["event_id"]: obs for obs in record["observations"]}
    assert observations["review-comment-501"]["classification"] == scanner.CLASS_FALSE_POSITIVE
    assert observations["review-comment-500"]["classification"] is None
    assert record["status"] == scanner.PR_STATUS_FALSE_POSITIVE
    states = [item["state"] for item in record["ledger"]["items"]]
    assert "excluded" in states
    assert "insufficient-evidence" in states


def test_third_party_reply_cannot_dispose(tmp_path: Path) -> None:
    summary = make_pr_summary(1)
    transport = empty_transport()
    review_comments = [
        {"id": 700, "user": {"login": "reviewer-1"}, "body": "a finding", "path": "x.py", "line": 1},
        {
            "id": 701,
            "user": {"login": "someone-else"},
            "body": "not a bug",
            "path": "x.py",
            "line": 2,
            "in_reply_to_id": 700,
        },
    ]
    transport.route_pages("pulls/1/comments", [review_comments])
    record = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=make_registry(tmp_path, "DFF-111"),
        backfill_path=make_backfill(tmp_path),
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
    )
    assert record["observations"][0]["classification"] is None
    assert record["status"] == scanner.PR_STATUS_FINDING_EXTRACTED


# ---------------------------------------------------------------------------
# 12 + 18. Unresolved/neutral evidence and explicit status for every PR
# ---------------------------------------------------------------------------


def test_neutral_finding_is_extracted_with_explicit_status(tmp_path: Path) -> None:
    summary = make_pr_summary(1)
    transport = empty_transport()
    review_comments = [
        {
            "id": 900,
            "user": {"login": "reviewer-1"},
            "body": "possible issue",
            "path": "src/a.py",
            "line": 4,
            "original_line": 4,
        },
    ]
    transport.route_pages("pulls/1/comments", [review_comments])
    record = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=make_registry(tmp_path, "DFF-111"),
        backfill_path=make_backfill(tmp_path),
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
    )
    assert record["status"] == scanner.PR_STATUS_FINDING_EXTRACTED
    assert record["scan_state"] == scanner.SCAN_COMPLETE
    items = record["ledger"]["items"]
    assert items[0]["state"] == "insufficient-evidence"
    assert items[0]["observation"]["classification"] is None
    assert record["status_reason"].startswith("neutral observations await")


def test_governed_confirmed_without_family_proposes_new_family(tmp_path: Path) -> None:
    summary = make_pr_summary(1)
    transport = empty_transport()
    review_comments = [
        {
            "id": 901,
            "user": {"login": "reviewer-1"},
            "body": "security token in header",
            "path": "src/a.py",
            "line": 4,
            "original_line": 4,
        },
    ]
    transport.route_pages("pulls/1/comments", [review_comments])
    decisions = {
        "review-comment-901": {
            "classification": "confirmed",
            "invariant": "secrets-committed",
            "affected_paths": ["src/a.py"],
            "fix_reference": "PR#99",
            "regression_evidence": ["test_secrets.py"],
        }
    }
    record = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=make_registry(tmp_path, "DFF-111"),
        backfill_path=make_backfill(tmp_path),
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
        governed_classifications=decisions,
    )
    assert record["status"] == scanner.PR_STATUS_CONFIRMED_DEFECT
    states = [item["state"] for item in record["ledger"]["items"]]
    assert states == ["candidate-new-family"]
    assert record["ledger"]["items"][0]["proposal"]["canonical_write_authorized"] is False


def test_governed_confirmed_to_existing_family_and_ambiguous(tmp_path: Path) -> None:
    registry = make_registry(tmp_path, "DFF-111")
    summary = make_pr_summary(1)
    transport = empty_transport()
    review_comments = [
        {
            "id": 911,
            "user": {"login": "reviewer-1"},
            "body": "a file write issue",
            "path": "src/components/1/a.py",
            "line": 4,
            "original_line": 4,
        },
    ]
    transport.route_pages("pulls/1/comments", [review_comments])
    existing = {
        "review-comment-911": {
            "classification": "confirmed",
            "invariant": "invariant-1",
            "affected_paths": ["src/components/1/a.py"],
            "fix_reference": "PR#42",
            "regression_evidence": ["test_write.py"],
            "claimed_family_id": "DFF-111",
        }
    }
    record = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=registry,
        backfill_path=make_backfill(tmp_path),
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
        governed_classifications=existing,
    )
    assert record["status"] == scanner.PR_STATUS_DUPLICATE_DEFECT
    assert record["ledger"]["items"][0]["state"] == "existing-family"
    assert record["ledger"]["items"][0]["proposal"]["canonical_family_id"] == "DFF-111"

    transport2 = empty_transport()
    transport2.route_pages(
        "pulls/1/comments",
        [review_comments],
    )
    missing_family = {
        "review-comment-911": {
            "classification": "confirmed",
            "invariant": "invariant-1",
            "affected_paths": ["src/components/1/a.py"],
            "fix_reference": "PR#42",
            "regression_evidence": ["test_write.py"],
            "claimed_family_id": "DFF-999",
        }
    }
    record2 = scanner.process_pr(
        transport2,
        REPO,
        "tok",
        summary,
        registry_path=registry,
        backfill_path=make_backfill(tmp_path),
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
        governed_classifications=missing_family,
    )
    assert record2["status"] == scanner.PR_STATUS_UNRESOLVED
    assert record2["ledger"]["items"][0]["state"] == "ambiguous"


def test_provider_unavailable_observation_excluded(tmp_path: Path) -> None:
    summary = make_pr_summary(1)
    transport = empty_transport()
    review_comments = [
        {"id": 950, "user": {"login": "reviewer-1"}, "body": "ci flake only", "path": "src/x.py", "line": 1}
    ]
    transport.route_pages("pulls/1/comments", [review_comments])
    record = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=make_registry(tmp_path, "DFF-111"),
        backfill_path=make_backfill(tmp_path),
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
        governed_classifications={"review-comment-950": {"classification": "provider-unavailable"}},
    )
    assert record["status"] == scanner.PR_STATUS_INFRA_PROVIDER_FAILURE
    assert record["ledger"]["items"][0]["state"] == "excluded"


def test_owner_style_disposition_is_non_defect(tmp_path: Path) -> None:
    summary = make_pr_summary(1)
    transport = empty_transport()
    review_comments = [
        {"id": 960, "user": {"login": "reviewer-1"}, "body": "string concat", "path": "src/x.py", "line": 1},
        {
            "id": 961,
            "user": {"login": "owner-1"},
            "body": "nit, naming suggestion only",
            "path": "src/x.py",
            "line": 2,
            "in_reply_to_id": 960,
        },
    ]
    transport.route_pages("pulls/1/comments", [review_comments])
    record = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=make_registry(tmp_path, "DFF-111"),
        backfill_path=make_backfill(tmp_path),
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
    )
    assert record["status"] == scanner.PR_STATUS_STYLE_OR_NON_DEFECT


# ---------------------------------------------------------------------------
# 13. Provenance preservation
# ---------------------------------------------------------------------------


def test_raw_evidence_preserved_verbatim(tmp_path: Path) -> None:
    summary = make_pr_summary(1)
    transport = empty_transport()
    raw_comment = {
        "id": 1,
        "user": {"login": "reviewer-1"},
        "body": "the raw finding",
        "path": "src/b.py",
        "line": 9,
        "original_line": 9,
        "commit_id": sha("c1"),
    }
    transport.route_pages("pulls/1/comments", [[raw_comment]])
    record = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=make_registry(tmp_path, "DFF-111"),
        backfill_path=make_backfill(tmp_path),
        snapshot_captured_at="snapshot-ts",
        owner_login="owner-1",
        collect_statuses=False,
    )
    assert record["evidence"]["review_comments"] == [raw_comment]
    observation = record["observations"][0]
    assert observation["event_id"] == "review-comment-1"
    assert observation["message"] == "the raw finding"
    assert observation["reviewed_head_sha"] == summary["head_sha"]
    assert observation["reviewed_base_sha"] == summary["base_sha"]
    assert record["snapshot_captured_at"] == "snapshot-ts"
    assert record["ledger"]["ledger_digest"]


# ---------------------------------------------------------------------------
# 14. Existing HBF compatibility (canonical historical_events identity)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not CANONICAL_BACKFILL.is_file() or not CANONICAL_REGISTRY.is_file(),
    reason="canonical backfill registry must exist",
)
def test_preload_translation_matches_canonical_historical_events(tmp_path: Path) -> None:
    raw = json.loads(CANONICAL_BACKFILL.read_text(encoding="utf-8"))
    records = raw["records"]
    canonical_source_prs = sorted({r["source_pr"] for r in records})
    assert canonical_source_prs, "canonical backfill has no records"
    head, base = sha("head"), sha("base")
    canonical = {event["source_pr"] for event in historical_events(head, base)}
    assert canonical_source_prs == sorted(canonical)
    for pr_number in canonical_source_prs:
        translated = scanner.preload_historical_observations(
            pr_number, head, base, CANONICAL_BACKFILL, CANONICAL_REGISTRY
        )
        canonical_events = [event for event in historical_events(head, base) if event["source_pr"] == pr_number]
        assert translated == canonical_events


@pytest.mark.skipif(
    not CANONICAL_BACKFILL.is_file() or not CANONICAL_REGISTRY.is_file(),
    reason="canonical backfill registry must exist",
)
def test_canonical_hbf_pr_maps_to_existing_family(tmp_path: Path) -> None:
    raw = json.loads(CANONICAL_BACKFILL.read_text(encoding="utf-8"))
    pr_number = raw["records"][0]["source_pr"]
    summary = make_pr_summary(pr_number)
    transport = empty_transport()
    route_empty_pr(transport, pr_number)
    record = scanner.process_pr(
        transport,
        REPO,
        "tok",
        summary,
        registry_path=CANONICAL_REGISTRY,
        backfill_path=CANONICAL_BACKFILL,
        snapshot_captured_at="now",
        owner_login="owner-1",
        collect_statuses=False,
    )
    assert record["observations"], "canonical HBF should supply observations"
    states = {item["state"] for item in record["ledger"]["items"]}
    assert "existing-family" in states
    mapped = next(item for item in record["ledger"]["items"] if item["state"] == "existing-family")
    assert mapped["proposal"]["canonical_family_id"].startswith("DFF-")
    assert mapped["proposal"]["canonical_write_authorized"] is False


# ---------------------------------------------------------------------------
# 15/17. Coverage accounting, gap detection, out-of-scope status
# ---------------------------------------------------------------------------


def test_coverage_manifest_accounting_with_mixed_statuses(tmp_path: Path) -> None:
    summaries = [make_pr_summary(1), make_pr_summary(2), make_pr_summary(3)]
    transport = empty_transport()
    transport.route_pages(
        "pulls/3/comments",
        [[{"id": 3, "user": {"login": "reviewer-1"}, "body": "a finding", "path": "src/a.py", "line": 1}]],
    )
    result = run_scan(
        tmp_path,
        summaries,
        transport,
        out_of_scope={2: "documented dependency PR"},
    )
    numbers = [1, 2, 3]
    records = [scanner.load_record(tmp_path, n) for n in numbers]
    assert all(record is not None for record in records)
    for n in (1, 2, 3):
        assert record_checked(tmp_path, n)["status"] in scanner.PR_STATUSES
    assert record_checked(tmp_path, 2)["status"] == scanner.PR_STATUS_OUT_OF_SCOPE
    manifest = scanner.build_coverage_manifest(
        repository=REPO,
        initial_snapshot={"prs": summaries, "total_prs": 3, "snapshot_id": "i", "captured_at_utc": "now"},
        final_snapshot={"prs": summaries, "total_prs": 3, "snapshot_id": "f", "captured_at_utc": "now"},
        records=records,
        batches_completed=result["batches_completed"],
        batches_total=result["batches_total"],
        last_checkpoint="done",
    )
    assert manifest["total_prs_in_final_scope"] == 3
    assert manifest["total_statused"] == 3
    assert sum(manifest["status_counts"].values()) == 3
    assert manifest["status_counts"][scanner.PR_STATUS_OUT_OF_SCOPE] == 1
    assert manifest["status_counts"][scanner.PR_STATUS_FINDING_EXTRACTED] == 1
    assert manifest["status_counts"][scanner.PR_STATUS_SCANNED_NO_FINDING] == 1
    assert manifest["coverage_gap"] == 0
    assert manifest["complete"] is True
    assert manifest["infra_provider_exclusion_count"] == 0


def test_coverage_gap_detected_when_record_missing(tmp_path: Path) -> None:
    summaries = [make_pr_summary(1), make_pr_summary(2)]
    transport = empty_transport()
    run_scan(tmp_path, summaries, transport)
    os_replacement = tmp_path / "prs" / "2.json"
    os_replacement.unlink()
    records = [scanner.load_record(tmp_path, 1)]
    manifest = scanner.build_coverage_manifest(
        repository=REPO,
        initial_snapshot={"prs": summaries, "total_prs": 2, "snapshot_id": "i", "captured_at_utc": "now"},
        final_snapshot={"prs": summaries, "total_prs": 2, "snapshot_id": "f", "captured_at_utc": "now"},
        records=records,
        batches_completed=1,
        batches_total=1,
        last_checkpoint="x",
    )
    assert manifest["coverage_gap"] == 1
    assert manifest["complete"] is False
    assert manifest["missing_status_records"] == [2]


# ---------------------------------------------------------------------------
# 16. Catch-up pass closes the gap for new PRs
# ---------------------------------------------------------------------------


def test_catch_up_manifest_closes_gap_for_new_prs(tmp_path: Path) -> None:
    initial = [make_pr_summary(1), make_pr_summary(2)]
    transport = empty_transport()
    run_scan(tmp_path, initial, transport)
    assert scanner.load_record(tmp_path, 1) is not None
    merged = initial + [make_pr_summary(3)]
    transport.route_pages("pulls/3/files", [[]])
    run_scan(tmp_path, merged, transport)  # reprocesses all, idempotent, includes new PR 3
    records = [scanner.load_record(tmp_path, n) for n in (1, 2, 3)]
    manifest = scanner.build_coverage_manifest(
        repository=REPO,
        initial_snapshot={"prs": initial, "total_prs": 2, "snapshot_id": "init", "captured_at_utc": "t0"},
        final_snapshot={"prs": merged, "total_prs": 3, "snapshot_id": "final", "captured_at_utc": "t1"},
        records=records,
        batches_completed=2,
        batches_total=2,
        last_checkpoint="catch-up",
    )
    assert manifest["catch_up_pr_count"] == 1
    assert manifest["total_prs_in_final_scope"] == 3
    assert manifest["coverage_gap"] == 0
    assert manifest["complete"] is True
    assert "final" == manifest["final_snapshot"]["id"]


# ---------------------------------------------------------------------------
# CLI wiring (no token required for --report / arg validation)
# ---------------------------------------------------------------------------


def test_cli_rejects_invalid_args() -> None:
    with pytest.raises(SystemExit, match="batch-size"):
        cli.main(["--batch-size", "0"])
    with pytest.raises(SystemExit, match="max-pages"):
        cli.main(["--max-pages", "0"])


def test_cli_report_missing_artifacts(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no scan artifacts"):
        cli.main(["--report", "--data-dir", str(tmp_path)])


def test_cli_report_prints_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    summaries = [make_pr_summary(1)]
    transport = empty_transport()
    run_scan(
        tmp_path, summaries, transport, registry=make_registry(tmp_path, "DFF-111"), backfill=make_backfill(tmp_path)
    )
    build_snapshot_file(tmp_path, summaries)
    capsys.readouterr()
    assert cli.main(["--report", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["complete"] is True
    assert data["coverage_gap"] == 0
    assert data["status_counts"][scanner.PR_STATUS_SCANNED_NO_FINDING] == 1


# ---------------------------------------------------------------------------
# derive_pr_status ordering sanity (explicit single status invariant)
# ---------------------------------------------------------------------------


def test_derive_pr_status_single_status_invariant(tmp_path: Path) -> None:
    summaries = [make_pr_summary(n) for n in (1, 2, 3, 4)]
    transport = empty_transport()
    transport.route_pages(
        "pulls/1/comments",
        [[{"id": 1, "user": {"login": "reviewer-1"}, "body": "unclear", "path": "a.py", "line": 1}]],
    )
    result = run_scan(tmp_path, summaries, transport)
    statuses = [record_checked(tmp_path, n)["status"] for n in (1, 2, 3, 4)]
    assert all(status in scanner.PR_STATUSES for status in statuses)
    assert statuses[0] == scanner.PR_STATUS_FINDING_EXTRACTED
    assert statuses[1:] == [scanner.PR_STATUS_SCANNED_NO_FINDING] * 3
    assert result["completed_prs"] == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# transport-level upload policy sanity (worker is read-only)
# ---------------------------------------------------------------------------


def test_worker_never_sends_mutations(tmp_path: Path) -> None:
    summaries = [make_pr_summary(1)]
    transport = empty_transport()
    run_scan(tmp_path, summaries, transport)
    mutations = [method for _, method in transport.calls if method != "GET"]
    assert mutations == []
    assert transport.calls, "worker should have issued GET reads"


# ---------------------------------------------------------------------------
# 18. Scanner authentication seam: global vs repository-scoped request scope
#
# The scanner used to verify its token by asking the repository-scoped
# governed seam for "rate_limit", which addresses
# /repos/{repository}/rate_limit. That sub-resource does not exist, so GitHub
# answers a permanent 404 and a valid token is reported as if it were
# unauthorized. These tests pin both scopes so the two cannot be conflated
# again, and pin that the global seam keeps the governed failure
# classification and fails closed.
# ---------------------------------------------------------------------------


def _governed_modules() -> tuple[Any, Any]:
    import hunter_github_transport as gh_transport
    import hunter_governance_review_v2 as governance

    return governance, gh_transport


def _rate_limit_payload(remaining: int = 4999, limit: int = 5000) -> dict[str, Any]:
    return {"resources": {"core": {"remaining": remaining, "limit": limit}}}


def test_auth_verification_uses_global_rate_limit_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authentication must address GitHub's global /rate_limit, not a repo sub-resource."""
    governance, _ = _governed_modules()
    global_calls: list[tuple[Any, ...]] = []
    repository_calls: list[tuple[Any, ...]] = []

    def global_stub(*args: Any) -> Any:
        global_calls.append(args)
        return _rate_limit_payload()

    def repository_stub(*args: Any) -> Any:
        repository_calls.append(args)
        return None

    monkeypatch.setattr(governance, "request_global_json", global_stub)
    monkeypatch.setattr(governance, "request_json", repository_stub)

    cli._verify_authentication(cli._global_request(), REPO, "tok")

    assert global_calls == [("tok", "GET", "rate_limit")]
    assert repository_calls == [], "authentication must not use the repository-scoped seam"


def test_global_seam_addresses_global_url_through_governed_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """The global seam still crosses transport.request_rest_json, with the global URL."""
    governance, _ = _governed_modules()
    seen: list[dict[str, Any]] = []

    def stub(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return _rate_limit_payload()

    monkeypatch.setattr(governance.transport, "request_rest_json", stub)

    governance.request_global_json("tok", "GET", "rate_limit")

    assert len(seen) == 1
    assert seen[0]["url"] == "https://api.github.com/rate_limit"
    assert seen[0]["method"] == "GET"
    assert seen[0]["token"] == "tok"
    assert seen[0]["data"] is None
    assert seen[0]["what"] == "GET rate_limit"


def test_repository_scoped_request_behavior_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repository-scoped paths still resolve under /repos/{repository}."""
    governance, _ = _governed_modules()
    seen: list[dict[str, Any]] = []

    def repo_stub(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return {"owner": {"login": "owner-1"}}

    monkeypatch.setattr(governance.transport, "request_rest_json", repo_stub)

    assert cli._request()(REPO, "tok", "GET", "") == {"owner": {"login": "owner-1"}}
    assert seen[0]["url"] == f"https://api.github.com/repos/{REPO}"

    governance.request_json(REPO, "tok", "GET", "pulls/1")
    assert seen[1]["url"] == f"https://api.github.com/repos/{REPO}/pulls/1"


def test_repository_scoped_seam_does_not_silently_absorb_global_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repository-scoped helper must not quietly route a global path correctly.

    The wrong-scope URL is the defect: it must stay observable at this seam
    rather than being auto-corrected, so callers are forced onto the
    explicit global seam.
    """
    governance, _ = _governed_modules()
    seen: list[dict[str, Any]] = []

    def stub(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return _rate_limit_payload()

    monkeypatch.setattr(governance.transport, "request_rest_json", stub)

    governance.request_json(REPO, "tok", "GET", "rate_limit")

    assert seen[0]["url"] == f"https://api.github.com/repos/{REPO}/rate_limit"


def test_global_seam_is_read_only_and_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The global seam cannot become a second unreviewed way to reach GitHub."""
    governance, _ = _governed_modules()
    called: list[dict[str, Any]] = []

    def must_not_run(**kwargs: Any) -> Any:
        called.append(kwargs)
        return _rate_limit_payload()

    monkeypatch.setattr(governance.transport, "request_rest_json", must_not_run)

    with pytest.raises(ValueError, match="read-only"):
        governance.request_global_json("tok", "POST", "rate_limit")
    with pytest.raises(ValueError, match="not a governed global REST endpoint"):
        governance.request_global_json("tok", "GET", "user")
    assert called == [], "a refused global request must not reach the transport"


def test_authentication_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Permanent and unavailable auth failures stay typed scan errors."""
    governance, gh_transport = _governed_modules()

    def refuse(*_args: Any) -> Any:
        raise gh_transport.GitHubRequestError("GitHub HTTP 401: Bad credentials", category="permanent")

    monkeypatch.setattr(governance, "request_global_json", refuse)
    with pytest.raises(scanner.ScanPermanentError):
        cli._verify_authentication(cli._global_request(), REPO, "bad-token")

    def unavailable(*_args: Any) -> Any:
        raise gh_transport.GitHubUnavailable(
            "GET rate_limit",
            attempts=3,
            last=gh_transport.GitHubRequestError("GitHub HTTP 503", category="transient", status_code=503),
        )

    monkeypatch.setattr(governance, "request_global_json", unavailable)
    with pytest.raises(scanner.ScanInfrastructureUnavailable):
        cli._verify_authentication(cli._global_request(), REPO, "tok")


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([], id="not-a-mapping"),
        pytest.param({}, id="missing-resources"),
        pytest.param({"resources": []}, id="resources-not-a-mapping"),
    ],
)
def test_authentication_malformed_payload_fails_closed(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    """A malformed rate_limit body must not be read as a successful auth check."""
    governance, _ = _governed_modules()
    monkeypatch.setattr(governance, "request_global_json", lambda *args: payload)

    with pytest.raises(SystemExit):
        cli._verify_authentication(cli._global_request(), REPO, "tok")


def test_global_seam_preserves_transient_retry_then_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 on /rate_limit retries boundedly, then fails closed as unavailable."""
    import io
    import urllib.error

    governance, gh_transport = _governed_modules()
    real_request_rest_json = gh_transport.request_rest_json
    attempts: list[int] = []

    def fast_retry(**kwargs: Any) -> Any:
        kwargs["sleeper"] = lambda _seconds: None
        return real_request_rest_json(**kwargs)

    def always_rate_limited(**_kwargs: Any) -> Any:
        attempts.append(1)
        raise urllib.error.HTTPError(
            "https://api.github.com/rate_limit",
            429,
            "too many requests",
            {},
            io.BytesIO(b'{"message": "API rate limit exceeded"}'),
        )

    monkeypatch.setattr(gh_transport, "request_rest_json", fast_retry)
    monkeypatch.setattr(governance.transport, "rest_json", always_rate_limited)

    with pytest.raises(scanner.ScanInfrastructureUnavailable):
        cli._verify_authentication(cli._global_request(), REPO, "tok")
    assert len(attempts) == gh_transport.DEFAULT_RETRY_ATTEMPTS


def test_global_seam_keeps_permanent_status_unretried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 on the global endpoint is permanent, not retried into unavailability."""
    import io
    import urllib.error

    governance, gh_transport = _governed_modules()
    real_request_rest_json = gh_transport.request_rest_json
    attempts: list[int] = []

    def fast_retry(**kwargs: Any) -> Any:
        kwargs["sleeper"] = lambda _seconds: None
        return real_request_rest_json(**kwargs)

    def not_found(**_kwargs: Any) -> Any:
        attempts.append(1)
        raise urllib.error.HTTPError(
            "https://api.github.com/rate_limit",
            404,
            "not found",
            {},
            io.BytesIO(b'{"message": "Not Found"}'),
        )

    monkeypatch.setattr(gh_transport, "request_rest_json", fast_retry)
    monkeypatch.setattr(governance.transport, "rest_json", not_found)

    with pytest.raises(scanner.ScanPermanentError):
        cli._verify_authentication(cli._global_request(), REPO, "tok")
    assert len(attempts) == 1, "a permanent status must not be retried"


# ---------------------------------------------------------------------------
# 19. Report describes the run that happened, not the default batch size
#
# --report derived batches_total from scanner.DEFAULT_BATCH_SIZE while the run
# persisted its own batch size, so a run executed with a non-default
# --batch-size reported a total that contradicted its own checkpoints.
# ---------------------------------------------------------------------------


def test_report_batches_total_uses_persisted_run_batch_size(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    summaries = [make_pr_summary(n) for n in range(1, 7)]
    transport = empty_transport()
    run_scan(
        tmp_path,
        summaries,
        transport,
        registry=make_registry(tmp_path, "DFF-111"),
        backfill=make_backfill(tmp_path),
        batch_size=2,
    )
    build_snapshot_file(tmp_path, summaries)

    persisted = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert persisted["batch_size"] == 2
    assert persisted["batch_size"] != scanner.DEFAULT_BATCH_SIZE, "test must exercise a non-default batch size"

    capsys.readouterr()
    assert cli.main(["--report", "--data-dir", str(tmp_path)]) == 0
    summary = json.loads(capsys.readouterr().out)["checkpoint_summary"]

    assert summary["batches_completed"] == 3
    assert summary["batches_total"] == 3, "report must reflect the run's batch size, not the default"


def test_report_batches_total_defaults_when_no_run_progress(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """With snapshots but no progress, the report falls back to the default size."""
    summaries = [make_pr_summary(n) for n in range(1, 4)]
    build_snapshot_file(tmp_path, summaries)
    for number in (1, 2, 3):
        scanner.store_record(
            tmp_path,
            {
                "pr_number": number,
                "status": scanner.PR_STATUS_SCANNED_NO_FINDING,
                "record_digest": sha(f"r{number}"),
            },
        )
    capsys.readouterr()
    assert cli.main(["--report", "--data-dir", str(tmp_path)]) == 0
    summary = json.loads(capsys.readouterr().out)["checkpoint_summary"]

    assert summary["batches_completed"] == 0
    assert summary["batches_total"] == len(scanner.partition_batches([1, 2, 3], scanner.DEFAULT_BATCH_SIZE))
