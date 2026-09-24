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


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        raise SystemExit("GITHUB_TOKEN is required for any GitHub-backed operation")
    return token


def _request() -> RestRequest:
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import importlib

    import hunter_governance_review_v2 as governance

    transport = importlib.import_module("hunter_github_transport")
    unavailable_type = getattr(transport, "GitHubUnavailable", RuntimeError)
    request_error_type = getattr(transport, "GitHubRequestError", RuntimeError)
    import urllib.error

    def request(repository: str, token: str, method: str, path: str) -> Any:
        """Adapter mapping the governed transport failure domain to scan states."""
        try:
            return governance.request_json(repository, token, method, path)
        except unavailable_type as exc:
            raise scanner.ScanInfrastructureUnavailable(str(exc)) from exc
        except request_error_type as exc:
            raise scanner.ScanPermanentError(str(exc)) from exc
        except urllib.error.URLError as exc:
            raise scanner.ScanInfrastructureUnavailable(str(exc)) from exc

    return request


def _verify_authentication(request: RestRequest, repository: str, token: str) -> None:
    payload = request(repository, token, "GET", "rate_limit")
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

    token = _token()
    request = _request()
    _verify_authentication(request, args.repository, token)
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
    batches_total = len(scanner.partition_batches(numbers, scanner.DEFAULT_BATCH_SIZE))
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
