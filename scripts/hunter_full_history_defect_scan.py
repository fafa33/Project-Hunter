#!/usr/bin/env python3
"""Full-history defect backfill worker CLI.

Deterministic, batched, checkpointed, resumable scan of the COMPLETE historical
PR/review/finding record of a GitHub repository, feeding trustworthy historical
defect knowledge into Hunter's canonical learning pipeline.

CLI behavior mirrors the mission requirements:

    --repository         owner/name of the repository to scan (default
                         fafa33/Project-Hunter)
    --data-dir           scan artifact directory (default
                         data/full_history_defect_scan)
    --batch-size         PRs per batch (default 20; recomputed batch sets are
                         checkpointed after every completed batch)
    --dry-run            real GitHub data, but only the first batch, into a
                         sealed dry_run/ subdirectory; never anything canonical
    --dry-run-limit      PRs processed in dry-run mode (default 5)
    --resume             continue an existing checkpointed run
    --fresh              erase and restart scan artifacts (authorization
                         required, never implies any GitHub mutation)
    --catch-up           capture a new final snapshot and process PRs created
                         after the initial snapshot until coverage_gap == 0
    --report             rebuild and print the coverage manifest from artifacts
    --adjudicate         re-adjudicate the frozen scan's ledger in place into
                         confirmed / mapped-to-family / excluded-with-evidence
                         or an explicit owner queue (no re-fetch, no --fresh)
    --owner-login        repository owner login; owner authority is the
                         adjudication signal for --adjudicate
    --collect-statuses   also preserve the head commit status payloads (CI
                         evidence; optional because it materially raises the
                         GitHub request budget)
    --classifications    path to owner-adjudicated classification decisions
                         (the only channel that can confirm a new observation)
    --out-of-scope       path to documented out-of-scope PR decisions
    --max-pages          per-endpoint page bound (default 5)
    --max-attempts       bounded worker-level retries for provider failures

The scan is strictly read-only against GitHub (GET only). No PR is mutated, no
review is posted, and no canonical defect authority is ever granted by this
worker.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from hunter.evidence_intelligence import full_history_defect_scan as scanner  # noqa: E402

DEFAULT_REPOSITORY = "fafa33/Project-Hunter"
DEFAULT_DATA_DIR = ROOT / "data" / "full_history_defect_scan"

RestRequest = Callable[..., Any]
GlobalRestRequest = Callable[..., Any]


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        raise SystemExit("GITHUB_TOKEN is required for any GitHub-backed operation")
    return token


def _governed() -> tuple[Any, Any, Any]:
    """Import the governed governance surface and its typed transport failures."""
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import importlib

    import hunter_governance_review_v2 as governance

    transport = importlib.import_module("hunter_github_transport")
    return (
        governance,
        getattr(transport, "GitHubUnavailable", RuntimeError),
        getattr(transport, "GitHubRequestError", RuntimeError),
    )


def _governed_call(call: Callable[[], Any], unavailable_type: Any, request_error_type: Any) -> Any:
    """Map the governed transport failure domain onto scan states.

    One mapping shared by every GitHub call this scanner makes, so a second
    request seam cannot introduce a weaker classification than the first.
    """
    import urllib.error

    try:
        return call()
    except unavailable_type as exc:
        raise scanner.ScanInfrastructureUnavailable(str(exc)) from exc
    except request_error_type as exc:
        raise scanner.ScanPermanentError(str(exc)) from exc
    except urllib.error.URLError as exc:
        raise scanner.ScanInfrastructureUnavailable(str(exc)) from exc


def _request() -> RestRequest:
    governance, unavailable_type, request_error_type = _governed()

    def request(repository: str, token: str, method: str, path: str) -> Any:
        """Repository-scoped adapter; ``path`` resolves under /repos/{repository}."""
        return _governed_call(
            lambda: governance.request_json(repository, token, method, path),
            unavailable_type,
            request_error_type,
        )

    return request


def _global_request() -> GlobalRestRequest:
    """Adapter for GitHub's global REST endpoints, which have no repo sub-resource."""
    governance, unavailable_type, request_error_type = _governed()

    def request(token: str, method: str, path: str) -> Any:
        return _governed_call(
            lambda: governance.request_global_json(token, method, path),
            unavailable_type,
            request_error_type,
        )

    return request


def _verify_authentication(request: GlobalRestRequest, repository: str, token: str) -> None:
    # /rate_limit is a global endpoint. Requesting it through the
    # repository-scoped seam addresses /repos/{repository}/rate_limit, which
    # does not exist and fails permanently with HTTP 404 -- a valid token
    # reported as if it were unauthorized.
    payload = request(token, "GET", "rate_limit")
    if not isinstance(payload, dict):
        raise SystemExit("GitHub rate_limit endpoint returned an unexpected payload")
    resources = payload.get("resources")
    if not isinstance(resources, dict):
        raise SystemExit("GitHub rate_limit payload is missing resources")
    core = resources.get("core")
    if isinstance(core, dict):
        remaining = core.get("remaining")
        limit = core.get("limit")
        print(f"auth: OK  rate_limit remaining={remaining}/{limit}")
        if isinstance(remaining, int) and isinstance(limit, int) and remaining < limit // 10:
            print(f"warning: only {remaining} GitHub requests remain; the full scan needs several thousand")
    print(f"repository: {repository}")
    print("worker: github-rest transport with bounded retry (429/5xx/transport) and fail-closed permanent errors")


def _repository_owner(request: RestRequest, repository: str, token: str) -> str:
    payload = request(repository, token, "GET", "")
    if not isinstance(payload, dict):
        raise SystemExit("repository metadata payload is unavailable")
    owner = payload.get("owner")
    if not isinstance(owner, dict):
        return ""
    return str(owner.get("login") or "")


def _summaries_by_number(snapshot: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {summary["number"]: summary for summary in snapshot["prs"]}


def _load_out_of_scope(path: Path | None) -> dict[int, str]:
    if path is None or not path.is_file():
        return {}
    raw = scanner.read_json_file(path)
    result: dict[int, str] = {}
    entries = raw.get("prs") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise SystemExit(f"{path} must declare a prs list")
    for entry in entries:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("number"), int)
            and isinstance(entry.get("reason"), str)
            and entry["reason"].strip()
        ):
            result[entry["number"]] = entry["reason"]
    return result


def _load_classifications(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    return dict(scanner.read_json_file(path))


def _resolve_inputs(args: argparse.Namespace) -> argparse.Namespace:
    if not Path(args.classifications).is_file():
        args.classifications = None
    if not Path(args.out_of_scope).is_file():
        args.out_of_scope = None
    return args


def run(args: argparse.Namespace) -> int:
    _resolve_inputs(args)
    data_dir = Path(args.data_dir).resolve()
    if args.dry_run:
        data_dir = data_dir / "dry_run"

    if args.report:
        return _report(args.repository, data_dir)

    if args.reconstruct:
        return _reconstruct(args, data_dir)

    if args.adjudicate:
        return _adjudicate(args, data_dir)

    token = _token()
    request = _request()
    _verify_authentication(_global_request(), args.repository, token)
    owner_login = _repository_owner(request, args.repository, token)

    registry_path = Path(args.registry).resolve()
    backfill_path = Path(args.backfill).resolve()
    if not registry_path.is_file():
        raise SystemExit(f"canonical registry not found: {registry_path}")
    if not backfill_path.is_file():
        raise SystemExit(f"historical backfill not found: {backfill_path}")

    out_of_scope = _load_out_of_scope(args.out_of_scope)
    governed_classifications = _load_classifications(args.classifications)
    if governed_classifications:
        print(f"classifications: applying {len(governed_classifications)} owner-adjudicated decision(s)")

    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = data_dir / "snapshot.json"
    progress = scanner.read_progress(data_dir)

    if args.fresh:
        if progress is not None or snapshot_path.is_file():
            print(f"fresh: erasing prior run artifacts under {data_dir}")
            shutil.rmtree(data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)
            progress = None
            snapshot_path = data_dir / "snapshot.json"
        else:
            print("fresh: nothing to erase")

    if snapshot_path.is_file():
        snapshot = scanner.read_json_file(snapshot_path, required=True)
    else:
        print("snapshot: capturing frozen PR list (open/closed/merged/draft)")
        snapshot = scanner.capture_snapshot(request, args.repository, token, max_pages=args.max_pages)
        scanner.atomic_write_json(snapshot_path, snapshot)
    assert isinstance(snapshot, dict)
    print(f"snapshot: {snapshot['total_prs']} PRs  id={snapshot['snapshot_id'][:12]}")

    resume = progress is not None
    if resume and not args.resume:
        raise SystemExit("existing checkpointed run found; pass --resume to continue or --fresh to erase and restart")
    if args.resume and not resume:
        print("resume: no prior progress found; starting from the frozen snapshot")

    summaries = _summaries_by_number(snapshot)
    numbers = sorted(summaries)
    batches = scanner.partition_batches(numbers, args.batch_size)
    if not batches:
        print("snapshot: empty repository; nothing to scan")
        scanner.write_progress(
            data_dir,
            repository=args.repository,
            snapshot_id=snapshot["snapshot_id"],
            snapshot_total=snapshot["total_prs"],
            batch_size=args.batch_size,
            next_batch=1,
            completed_prs=[],
            pending_prs=[],
        )
        return 0

    if args.dry_run:
        batches = scanner.partition_batches(numbers[: args.dry_run_limit], args.batch_size)

    result = scanner.run_batches(
        request,
        args.repository,
        token,
        data_dir=data_dir,
        snapshot=snapshot,
        batches=batches,
        registry_path=registry_path,
        backfill_path=backfill_path,
        owner_login=owner_login,
        collect_statuses=args.collect_statuses,
        max_pages=args.max_pages,
        max_attempts=args.max_attempts,
        resume=resume,
        out_of_scope=out_of_scope,
        governed_classifications=governed_classifications,
        summaries_by_number=summaries,
        progress=progress,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print("dry-run: complete (scratch artifacts only; nothing canonical, nothing on GitHub)")
        print(f"  processed {args.dry_run_limit} PR(s) into {data_dir}")
        return 0

    final_snapshot = snapshot
    if args.catch_up:
        final_snapshot = _capture_final(request, args.repository, token, data_dir, args.max_pages)
        result = _catch_up(
            request,
            args.repository,
            token,
            data_dir,
            snapshot,
            final_snapshot,
            result,
            args,
            registry_path,
            backfill_path,
            owner_login,
        )

    numbers = [summary["number"] for summary in final_snapshot["prs"]]
    records = _all_records(data_dir, numbers)
    manifest = scanner.build_coverage_manifest(
        repository=args.repository,
        initial_snapshot=snapshot,
        final_snapshot=final_snapshot,
        records=records,
        batches_completed=result["batches_completed"],
        batches_total=result["batches_total"],
        last_checkpoint=result.get("last_checkpoint") or "pre-batch",
    )
    path = data_dir / "full_history_coverage_manifest.json"
    scanner.atomic_write_json(path, manifest)
    print(f"manifest: {path}")
    _print_manifest_summary(manifest)
    if not manifest["complete"]:
        print("COVERAGE GAP IS NON-ZERO: historical backfill is NOT complete; retry --resume/--catch-up")
    return 0


def _capture_final(
    request: RestRequest,
    repository: str,
    token: str,
    data_dir: Path,
    max_pages: int,
) -> dict[str, Any]:
    final_path = data_dir / "final_snapshot.json"
    if final_path.is_file():
        print("catch-up: reusing final snapshot from disk")
        final = scanner.read_json_file(final_path, required=True)
        assert isinstance(final, dict)
        return final
    print("catch-up: capturing final snapshot ...")
    final = scanner.capture_snapshot(request, repository, token, max_pages=max_pages)
    scanner.atomic_write_json(final_path, final)
    return final


def _catch_up(
    request: RestRequest,
    repository: str,
    token: str,
    data_dir: Path,
    initial_snapshot: dict[str, Any],
    final_snapshot: dict[str, Any],
    result: dict[str, Any],
    args: argparse.Namespace,
    registry_path: Path,
    backfill_path: Path,
    owner_login: str,
) -> dict[str, Any]:
    initial_numbers = {summary["number"] for summary in initial_snapshot["prs"]}
    final_numbers = {summary["number"] for summary in final_snapshot["prs"]}
    new_numbers = sorted(final_numbers - initial_numbers)
    print(f"catch-up: {len(new_numbers)} PR(s) created after the initial snapshot")
    if not new_numbers:
        print("catch-up: nothing to process")
        return result
    summaries = _summaries_by_number(final_snapshot)
    new_summaries = {number: summaries[number] for number in new_numbers}
    batches = scanner.partition_batches(new_numbers, args.batch_size)
    catch = scanner.run_batches(
        request,
        repository,
        token,
        data_dir=data_dir,
        snapshot=final_snapshot,
        batches=batches,
        registry_path=registry_path,
        backfill_path=backfill_path,
        owner_login=owner_login,
        collect_statuses=args.collect_statuses,
        max_pages=args.max_pages,
        max_attempts=args.max_attempts,
        resume=False,
        out_of_scope=_load_out_of_scope(args.out_of_scope),
        governed_classifications=_load_classifications(args.classifications),
        summaries_by_number=new_summaries,
        progress=None,
    )
    return {
        "completed_prs": sorted(set(result["completed_prs"]) | set(catch["completed_prs"])),
        "status_counts": {
            status: result["status_counts"].get(status, 0) + catch["status_counts"].get(status, 0)
            for status in set(result["status_counts"]) | set(catch["status_counts"])
        },
        "findings_extracted_total": result["findings_extracted_total"] + catch["findings_extracted_total"],
        "next_batch": max(result["next_batch"], catch["next_batch"]),
        "batches_total": result["batches_total"] + catch["batches_total"],
        "batches_completed": result["batches_completed"] + catch["batches_completed"],
        "infra_retryable": sorted(set(result["infra_retryable"]) | set(catch["infra_retryable"])),
        "last_checkpoint": catch.get("last_checkpoint") or result.get("last_checkpoint"),
    }


def _all_records(data_dir: Path, numbers: list[int]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number in numbers:
        record = scanner.load_record(data_dir, number)
        if record is not None:
            records.append(record)
    return records


def _print_manifest_summary(manifest: dict[str, Any]) -> None:
    print(
        f"coverage: {manifest['total_statused']}/{manifest['total_prs_in_final_scope']}  "
        f"gap={manifest['coverage_gap']}  complete={manifest['complete']}"
    )
    for status in sorted(scanner.PR_STATUSES):
        count = manifest["status_counts"].get(status, 0)
        if count:
            print(f"  {status}: {count}")


def _report(repository: str, data_dir: Path) -> int:
    initial_path = data_dir / "snapshot.json"
    if not initial_path.is_file():
        raise SystemExit(f"no scan artifacts under {data_dir}; run the worker first")
    initial = scanner.read_json_file(initial_path, required=True)
    assert isinstance(initial, dict)
    final_path = data_dir / "final_snapshot.json"
    raw_final = scanner.read_json_file(final_path) if final_path.is_file() else None
    final = raw_final if isinstance(raw_final, dict) else initial
    numbers = [summary["number"] for summary in final["prs"]]
    records = _all_records(data_dir, numbers)
    progress = scanner.read_progress(data_dir)
    batches_completed = progress.get("next_batch", 1) - 1 if progress else 0
    # The batch size that produced this run is persisted with the run, so the
    # report describes the run that happened rather than the default size. A
    # report of a run executed with a non-default --batch-size previously
    # reported a total that did not match its own checkpoint count.
    run_batch_size = (progress or {}).get("batch_size") or scanner.DEFAULT_BATCH_SIZE
    batches_total = len(scanner.partition_batches(numbers, run_batch_size))
    manifest = scanner.build_coverage_manifest(
        repository=repository,
        initial_snapshot=initial,
        final_snapshot=final,
        records=records,
        batches_completed=batches_completed,
        batches_total=batches_total,
        last_checkpoint="report",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def _verdicts(path: str | None) -> dict[str, Any] | None:
    """Load a verdict file as a whole payload.

    The payload is passed through unwrapped so a single file can carry the
    verdicts and the cluster declarations that explain them; the readers unwrap
    the inner map themselves.
    """
    if not path:
        return None
    payload = scanner.read_json_file(Path(path), required=False)
    return payload if isinstance(payload, dict) else None


def _reconstruct(args: argparse.Namespace, data_dir: Path) -> int:
    """Reconstruct repository-owned disposition evidence from local history.

    Reads the frozen scan and the local integration base only. No re-fetch, no
    rescan, no checkpoint mutation, and no write to canonical history: new
    families are returned as proposals for the canonicalization phase.
    """
    import importlib

    reconstruction = importlib.import_module("hunter.evidence_intelligence.historical_evidence_reconstruction")
    registry_path = Path(args.registry).resolve()
    if not registry_path.is_file():
        raise SystemExit(f"canonical registry not found: {registry_path}")
    if not args.owner_login:
        raise SystemExit("--owner-login is required: repository-owned authority is the disposition signal")
    manifest = reconstruction.run_reconstruction(
        data_dir=data_dir,
        registry_path=registry_path,
        owner_login=args.owner_login,
        repository=args.repository,
        repo_root=ROOT,
        ref=args.base_ref,
        resume=not args.no_resume_reconstruction,
        recurrence_verdicts=_verdicts(args.recurrence_verdicts),
        invariant_verdicts=_verdicts(args.invariant_verdicts),
    )
    print(f"reconstructed: {manifest['reconciliation']['reconstructed_items']} ledger items")
    print(f"  reconciles exactly          : {manifest['reconciliation']['reconciles_exactly']}")
    reconciliation = manifest["reconciliation"]
    print(
        f"  summary container bodies    : {reconciliation['summary_container_bodies']}"
        f" -> {reconciliation['derived_claims']} derived claim(s)"
        f" (reconcile: {reconciliation['derived_claims_reconcile_exactly']})"
    )
    duplicates = (manifest["derived_claims"]["duplicate_of_existing_observation_count"]) or 0
    if duplicates:
        print(f"  already observed elsewhere   : {duplicates} carried claim(s) not re-derived")
    print("dispositions:")
    for name, count in manifest["dispositions"].items():
        print(f"  {name:34s} {count}")
    print("historical defect truth:")
    for name, count in manifest["historical_defect_truth"].items():
        print(f"  {name:34s} {count}")
    print("closure dimensions:")
    for key in (
        "scan_coverage_gap",
        "adjudication_coverage_gap",
        "canonical_mapping_gap",
        "unresolved_evidence_count",
    ):
        print(f"  {key:28s} {manifest[key]}")
    print(f"  {'coverage_gap':28s} {manifest['coverage_gap']}")
    non_recurrence = manifest.get("non_recurrence_resolution") or {}
    print(f"resolved non-recurring        : {non_recurrence.get('resolved_non_recurring', 0)}")
    for reason, count in (non_recurrence.get("withheld_reasons") or {}).items():
        print(f"  withheld: {reason} ({count})")
    proposals = manifest["family_proposals"]
    print(
        f"family mapping: {proposals['mapped_item_count']} item(s) to "
        f"{len(proposals['existing_family_mappings'])} existing family(ies); "
        f"{proposals['new_family_proposal_count']} new-family proposal(s)"
    )
    print(
        f"owner required: {manifest['owner_required']['count']} in "
        f"{manifest['owner_required']['group_count']} evidence-gap group(s)"
        f" ({manifest['owner_required']['ledger_item_count']} ledger item(s) + "
        f"{manifest['owner_required']['derived_claim_count']} derived claim(s))"
    )
    print(f"manifest: {data_dir / 'historical_closure_manifest.json'}")
    return 0 if manifest["coverage_gap"] == 0 else 2


def _adjudicate(args: argparse.Namespace, data_dir: Path) -> int:
    """Re-adjudicate the frozen scan's ledger in place. No re-fetch, no --fresh.

    This is the evidence phase the scan completion manifest cannot stand in for:
    it resolves the ledger items the scan left as ``insufficient-evidence`` into
    exactly one of confirmed / mapped-to-family / excluded-with-evidence, or an
    explicit owner queue. It reads only artifacts already on disk.
    """
    import importlib

    adjudication = importlib.import_module("hunter.evidence_intelligence.historical_evidence_adjudication")
    registry_path = Path(args.registry).resolve()
    if not registry_path.is_file():
        raise SystemExit(f"canonical registry not found: {registry_path}")
    owner_login = args.owner_login
    if not owner_login:
        raise SystemExit("--owner-login is required to adjudicate: owner authority is the adjudication signal")

    manifest = adjudication.run_adjudication(
        data_dir=data_dir,
        registry_path=registry_path,
        owner_login=owner_login,
        repository=args.repository,
        resume=not args.no_resume_adjudication,
    )
    print(f"adjudication: {manifest['reconciliation']['raw_scan_ledger_items']} ledger items re-adjudicated")
    print(f"  reconciles exactly          : {manifest['reconciliation']['reconciles_exactly']}")
    for name, count in manifest["dispositions"].items():
        print(f"  {name:24s}: {count}")
    print("closure dimensions:")
    for key in (
        "scan_coverage_gap",
        "adjudication_coverage_gap",
        "canonical_mapping_gap",
        "unresolved_evidence_count",
    ):
        print(f"  {key:28s}: {manifest[key]}")
    print(f"  {'coverage_gap':28s}: {manifest['coverage_gap']}")
    queue = json.loads((data_dir / "adjudication" / "owner_decision_queue.json").read_text(encoding="utf-8"))["queue"]
    print(
        f"owner queue: {queue['ambiguous_item_count']} ambiguous item(s) " f"in {queue['group_count']} grouped case(s)"
    )
    print(f"manifest: {data_dir / 'historical_closure_manifest.json'}")
    return 0 if manifest["coverage_gap"] == 0 else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Full-history defect backfill worker")
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--registry", default=str(ROOT / "docs" / "DEFECT_REGISTRY.json"))
    parser.add_argument("--backfill", default=str(ROOT / "docs" / "HISTORICAL_DEFECT_BACKFILL.json"))
    parser.add_argument("--batch-size", type=int, default=scanner.DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dry-run-limit", type=int, default=5)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--catch-up", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument(
        "--adjudicate",
        action="store_true",
        help="re-adjudicate the frozen scan's ledger in place (no re-fetch, no --fresh)",
    )
    parser.add_argument(
        "--owner-login",
        default=os.environ.get("GITHUB_OWNER_LOGIN") or "",
        help="repository owner login; owner authority is the adjudication signal",
    )
    parser.add_argument("--no-resume-adjudication", action="store_true")
    parser.add_argument(
        "--reconstruct",
        action="store_true",
        help="reconstruct repository-owned disposition evidence for every ledger item "
        "(local history only; no re-fetch, no rescan, no canonical write)",
    )
    parser.add_argument(
        "--base-ref",
        default="origin/main",
        help="integration base used as the anchor for surviving implementation",
    )
    parser.add_argument("--no-resume-reconstruction", action="store_true")
    parser.add_argument(
        "--recurrence-verdicts",
        help="JSON file of recorded judgements about extracted invariant candidates, "
        "keyed by observation id. A candidate without a verdict stays unverified.",
    )
    parser.add_argument(
        "--invariant-verdicts",
        help="JSON file of recorded judgements about extracted invariant candidates, keyed by "
        "observation id. A candidate with no verdict stays unverified, so nothing is resolved "
        "on an unexamined extraction.",
    )
    parser.add_argument("--collect-statuses", action="store_true")
    parser.add_argument(
        "--classifications",
        type=str,
        default=str(DEFAULT_DATA_DIR / "classification_decisions.json"),
    )
    parser.add_argument("--out-of-scope", type=str, default=str(DEFAULT_DATA_DIR / "out_of_scope.json"))
    parser.add_argument("--max-pages", type=int, default=scanner.DEFAULT_MAX_PAGES)
    parser.add_argument("--max-attempts", type=int, default=scanner.DEFAULT_MAX_ATTEMPTS)
    args = parser.parse_args(argv)
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if args.dry_run_limit <= 0:
        raise SystemExit("--dry-run-limit must be positive")
    if args.max_pages <= 0 or args.max_attempts <= 0:
        raise SystemExit("--max-pages and --max-attempts must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
