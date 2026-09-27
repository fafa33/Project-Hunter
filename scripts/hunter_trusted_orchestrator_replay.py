"""Trusted Candidate Orchestrator Replay Harness.

Resolves the trust-boundary bootstrap deadlock: ``hunter_review_orchestrator.py``,
``hunter_reviewer_collector.py`` and ``hunter_governance_review_v2.py`` execute
only from the trusted default branch, by deliberate design, so a candidate PR
that changes this controller logic can never exercise its own change from its
own live CI. This harness replays the candidate's controller modules against
fixed, offline, deterministic scenario fixtures inside a read-only job with no
write credentials (``run``), and a separate job with no candidate execution at
all independently re-derives every trusted-side digest before publishing a
status (``validate``). See ``.github/workflows/hunter-trusted-orchestrator-replay.yml``.

What a green replay proves, and what it does not
-------------------------------------------------
PROVEN by a passing replay:
  * The candidate's ``independent_review_opportunity_seconds`` is derived from,
    and covers, its own real reviewer-pool configuration (scenario A).
  * The candidate's ``ensure_collector`` dispatches an exact-head collector at
    most once under a modeled read-your-write-stale reconcile race, and
    recovers cleanly once the read catches up (scenario B).
  * The candidate's real WAITING_FOR_REVIEWER -> pending classification path,
    end to end from ``pending_review_authority_state`` through
    ``review_orchestration_state``, resolves correctly for absent, in-progress
    and malformed cycle evidence (scenario G).

NOT proven, and never claimed, by a passing replay:
  * Real GitHub API latency or propagation delay.
  * Real reviewer-provider (Codex/Copilot/Gemini/Groq) response latency or
    availability.
  * Real webhook delivery timing.
  * Anything about behavior outside the three fixed scenarios above.
These remain live, post-merge canary evidence: only a real merged head,
observed running the real workflows against real GitHub state, can establish
them. This harness never publishes, and no production authority resolver
(Merge Readiness, Governance Review, candidate admission) may ever consult,
a claim that replay success is equivalent to that live evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKER_PATH = ROOT / "scripts" / "hunter_trusted_orchestrator_replay_worker.py"
TRUSTED_DEFINITION_FILES: tuple[str, ...] = (
    "scripts/hunter_trusted_orchestrator_replay.py",
    "scripts/hunter_trusted_orchestrator_replay_worker.py",
)

RECEIPT_SCHEMA = "hunter.trusted-orchestrator-replay.v1"
SCENARIO_SET_ID = "hunter-orchestrator-replay-v1"
REQUIRED_SCENARIO_IDS: tuple[str, ...] = ("A", "B", "G")
SCENARIO_INVARIANTS: dict[str, str] = {
    "A": (
        "The independent review opportunity is derived from, and at least "
        "covers, the candidate's own real reviewer-pool worst-case budget, "
        "with strictly positive reserved overhead and a bounded ceiling."
    ),
    "B": (
        "An exact-head collector is dispatched at most once across racing or "
        "repeated reconcile calls made while the combined-status read has not "
        "yet observed a prior write, and reconcile recovers cleanly once the "
        "read catches up."
    ),
    "G": (
        "A WAITING_FOR_REVIEWER exact-head cycle classifies to pending "
        "end-to-end through the real resolution path for absent and "
        "in-progress cycle evidence, and malformed cycle evidence fails "
        "closed rather than classifying as pending."
    ),
}
#: Fixed, deterministic, offline scenario input. Part of the trusted
#: definition: a candidate cannot supply its own fixture, and no field here
#: depends on wall-clock time, network state, or repository content outside
#: what each scenario explicitly reads from the candidate checkout.
FIXTURE: dict[str, Any] = {
    "timeout_delta_seconds": 9_000,
    "absolute_ceiling_seconds": 6 * 60 * 60,
    "head_sha": "b" * 40,
    "pr_number": 535,
    "config_digest": "f" * 64,
    "run_id": 4242,
}
#: Sanity bound on one scenario's own wall-clock duration. Generous: this
#: guards against a receipt claiming instantaneous or reversed timing, not
#: against ordinary CI variance.
MAX_SCENARIO_SECONDS = 300
WORKER_TIMEOUT_SECONDS = 120


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def trusted_harness_definition_digest() -> str:
    payload = {relative: (ROOT / relative).read_text(encoding="utf-8") for relative in TRUSTED_DEFINITION_FILES}
    return _digest(payload)


def scenario_set_digest() -> str:
    return _digest({"scenario_set_id": SCENARIO_SET_ID, "invariants": SCENARIO_INVARIANTS})


def fixture_digest() -> str:
    return _digest(FIXTURE)


def _trusted_harness_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _isolated_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["GITHUB_TOKEN"] = ""
    env["GH_TOKEN"] = ""
    return env


def _run_scenario(scenario_id: str, candidate_root: Path, fixture_path: Path) -> dict[str, Any]:
    started = datetime.now(UTC)
    process = subprocess.run(
        [
            sys.executable,
            str(WORKER_PATH),
            "--candidate-root",
            str(candidate_root),
            "--scenario",
            scenario_id,
            "--fixture",
            str(fixture_path),
        ],
        cwd=str(ROOT),
        env=_isolated_env(),
        capture_output=True,
        text=True,
        timeout=WORKER_TIMEOUT_SECONDS,
    )
    finished = datetime.now(UTC)
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    if process.returncode != 0 or not lines:
        return {
            "scenario_id": scenario_id,
            "invariant": SCENARIO_INVARIANTS[scenario_id],
            "outcome": "fail",
            "measurements": {},
            "error": f"worker exited {process.returncode}: {process.stderr.strip()[-2000:]}",
            "candidate_module_digest": "",
            "started_at": started.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "finished_at": finished.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
    payload = json.loads(lines[-1])
    payload["invariant"] = SCENARIO_INVARIANTS[scenario_id]
    payload["started_at"] = started.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    payload["finished_at"] = finished.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return payload


def build_receipt(*, candidate_root: Path, pr_number: int, candidate_sha: str) -> dict[str, Any]:
    fixture_path = candidate_root.parent / ".hunter-orchestrator-replay-fixture.json"
    fixture_path.write_text(json.dumps(FIXTURE, sort_keys=True), encoding="utf-8")
    try:
        scenario_results = [_run_scenario(sid, candidate_root, fixture_path) for sid in REQUIRED_SCENARIO_IDS]
    finally:
        fixture_path.unlink(missing_ok=True)

    overall = "pass" if all(result["outcome"] == "pass" for result in scenario_results) else "fail"
    return {
        "schema": RECEIPT_SCHEMA,
        "candidate_pr": pr_number,
        "candidate_sha": candidate_sha,
        "trusted_harness_sha": _trusted_harness_sha(),
        "trusted_harness_definition_digest": trusted_harness_definition_digest(),
        "scenario_set_id": SCENARIO_SET_ID,
        "scenario_set_digest": scenario_set_digest(),
        "input_fixture_digest": fixture_digest(),
        "scenario_results": scenario_results,
        "overall_result": overall,
    }


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def validate_receipt(
    receipt: Any,
    *,
    candidate_root: Path,
    pr_number: int,
    candidate_sha: str,
) -> list[str]:
    """Fail-closed validation. Every trusted quantity is recomputed here, never
    trusted from the receipt's own account of it; every candidate-controlled
    quantity (paths, filenames, scenario definitions, expected results, status
    context names) is likewise never taken from the receipt.
    """

    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["receipt is not a JSON object"]

    # 1. schema
    if receipt.get("schema") != RECEIPT_SCHEMA:
        errors.append(f"receipt schema {receipt.get('schema')!r} != {RECEIPT_SCHEMA!r}")
    # 2. candidate_pr bound to the trusted event's own PR number, never the receipt's say-so alone
    if receipt.get("candidate_pr") != pr_number:
        errors.append(f"receipt candidate_pr {receipt.get('candidate_pr')!r} != trusted PR #{pr_number}")
    # 3. candidate_sha bound to the trusted event's own head sha
    if receipt.get("candidate_sha") != candidate_sha:
        errors.append(f"receipt candidate_sha {receipt.get('candidate_sha')!r} != trusted head {candidate_sha!r}")
    # 4. trusted_harness_sha must equal this job's own trusted checkout HEAD
    try:
        expected_harness_sha = _trusted_harness_sha()
    except Exception as exc:  # noqa: BLE001
        return errors + [f"trusted harness SHA could not be resolved: {exc}"]
    if receipt.get("trusted_harness_sha") != expected_harness_sha:
        errors.append(
            f"receipt trusted_harness_sha {receipt.get('trusted_harness_sha')!r} != "
            f"this job's own trusted checkout {expected_harness_sha!r}"
        )
    # 5. trusted_harness_definition_digest recomputed from this job's own trusted files
    expected_definition_digest = trusted_harness_definition_digest()
    if receipt.get("trusted_harness_definition_digest") != expected_definition_digest:
        errors.append("receipt trusted_harness_definition_digest does not match the trusted harness definition")
    # 6. scenario_set_digest recomputed from the trusted canonical invariants, never from the receipt
    expected_scenario_digest = scenario_set_digest()
    if receipt.get("scenario_set_digest") != expected_scenario_digest:
        errors.append("receipt scenario_set_digest does not match the trusted canonical scenario definitions")
    # 7. input_fixture_digest recomputed from the trusted fixed fixture
    expected_fixture_digest = fixture_digest()
    if receipt.get("input_fixture_digest") != expected_fixture_digest:
        errors.append("receipt input_fixture_digest does not match the trusted fixed fixture")

    results = receipt.get("scenario_results")
    if not isinstance(results, list):
        return errors + ["receipt scenario_results must be a list"]

    by_id = {}
    for entry in results:
        if isinstance(entry, dict) and isinstance(entry.get("scenario_id"), str):
            by_id[entry["scenario_id"]] = entry
    # 8. exactly the required scenario ids, no substitution and no omission
    if set(by_id) != set(REQUIRED_SCENARIO_IDS):
        errors.append(
            f"receipt scenario_results must cover exactly {sorted(REQUIRED_SCENARIO_IDS)}, found {sorted(by_id)}"
        )

    for scenario_id in REQUIRED_SCENARIO_IDS:
        entry = by_id.get(scenario_id)
        if entry is None:
            continue
        # 9. invariant text must match the trusted canonical wording verbatim
        if entry.get("invariant") != SCENARIO_INVARIANTS[scenario_id]:
            errors.append(f"scenario {scenario_id} invariant text does not match the trusted canonical invariant")
        # 10. outcome must actually be pass
        if entry.get("outcome") != "pass":
            errors.append(f"scenario {scenario_id} outcome is {entry.get('outcome')!r}, not 'pass'")
        # 11. candidate_module_digest independently recomputed from this job's own candidate checkout
        import hunter_trusted_orchestrator_replay_worker as worker

        try:
            expected_module_digest = worker.candidate_module_digest(candidate_root, scenario_id)
        except OSError as exc:
            errors.append(f"scenario {scenario_id} candidate module files could not be read for verification: {exc}")
            continue
        if entry.get("candidate_module_digest") != expected_module_digest:
            errors.append(
                f"scenario {scenario_id} candidate_module_digest does not match this job's own "
                "independent recomputation from the candidate checkout"
            )
        # 12. timestamps must be present, parseable, non-reversed and boundedly short
        started = _parse_timestamp(entry.get("started_at"))
        finished = _parse_timestamp(entry.get("finished_at"))
        if started is None or finished is None:
            errors.append(f"scenario {scenario_id} timestamps are missing or unparseable")
        elif finished < started:
            errors.append(f"scenario {scenario_id} finished_at precedes started_at")
        elif (finished - started).total_seconds() > MAX_SCENARIO_SECONDS:
            errors.append(f"scenario {scenario_id} duration exceeds the {MAX_SCENARIO_SECONDS}s sanity ceiling")

    # 13. overall_result must be internally consistent with the individual scenario outcomes
    all_pass = bool(by_id) and all(by_id.get(sid, {}).get("outcome") == "pass" for sid in REQUIRED_SCENARIO_IDS)
    expected_overall = "pass" if all_pass else "fail"
    if receipt.get("overall_result") != expected_overall:
        errors.append(
            f"receipt overall_result {receipt.get('overall_result')!r} is inconsistent with its own "
            f"scenario outcomes (expected {expected_overall!r})"
        )

    return errors


def _cmd_run(args: argparse.Namespace) -> int:
    receipt = build_receipt(
        candidate_root=Path(args.candidate_root).resolve(),
        pr_number=args.pr,
        candidate_sha=args.candidate_sha,
    )
    Path(args.out).write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote replay receipt to {args.out}: overall_result={receipt['overall_result']}")
    return 0 if receipt["overall_result"] == "pass" else 1


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"replay receipt is unreadable or malformed: {exc}", file=sys.stderr)
        return 1
    errors = validate_receipt(
        receipt,
        candidate_root=Path(args.candidate_root).resolve(),
        pr_number=args.pr,
        candidate_sha=args.candidate_sha,
    )
    if errors:
        for error in errors:
            print(f"REPLAY VALIDATION FAILED: {error}", file=sys.stderr)
        return 1
    print("REPLAY VALIDATION PASSED")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Trusted candidate orchestrator replay harness")
    sub = result.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run.add_argument("--candidate-root", required=True)
    run.add_argument("--pr", type=int, required=True)
    run.add_argument("--candidate-sha", required=True)
    run.add_argument("--out", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--receipt", required=True)
    validate.add_argument("--candidate-root", required=True)
    validate.add_argument("--pr", type=int, required=True)
    validate.add_argument("--candidate-sha", required=True)

    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "run":
        return _cmd_run(args)
    return _cmd_validate(args)


if __name__ == "__main__":
    raise SystemExit(main())
