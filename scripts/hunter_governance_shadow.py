from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import hunter_github_transport as transport
import hunter_merge_readiness_v2 as readiness

DOMAINS = ("governance", "candidate-admission", "merge-readiness", "review-orchestration", "reviewer-collection")


def get_json(repo: str, token: str, path: str) -> Any:
    return transport.request_rest_json(
        url=f"https://api.github.com/repos/{repo}/{path}",
        method="GET",
        headers={},
        data=None,
        token=token,
        what=f"shadow GET {path}",
    )


def snapshot(repo: str, token: str, pr_number: int) -> dict[str, Any]:
    pr = get_json(repo, token, f"pulls/{pr_number}")
    if not isinstance(pr, dict):
        raise ValueError("malformed pull request")
    head = str((pr.get("head") or {}).get("sha") or "").strip()
    if not head:
        raise ValueError("missing head sha")
    checks = get_json(repo, token, f"commits/{head}/check-runs?per_page=100")
    statuses = get_json(repo, token, f"commits/{head}/statuses?per_page=100")
    reviews = get_json(repo, token, f"pulls/{pr_number}/reviews?per_page=100")
    return {
        "pr_number": pr_number,
        "head_sha": head,
        "draft": bool(pr.get("draft")),
        "mergeable": pr.get("mergeable"),
        "checks": (checks or {}).get("check_runs", []) if isinstance(checks, dict) else [],
        "statuses": statuses if isinstance(statuses, list) else [],
        "reviews": reviews if isinstance(reviews, list) else [],
    }


def _latest_status(statuses: list[dict[str, Any]], context: str) -> dict[str, Any] | None:
    matching = [s for s in statuses if str(s.get("context") or "") == context]
    return max(matching, key=lambda s: int(s.get("id") or 0)) if matching else None


def project(snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    head = snap["head_sha"]
    base = {"head_sha": head, "draft": snap["draft"], "mergeable": snap["mergeable"]}
    result = {domain: dict(base, domain=domain, semantic_state="UNKNOWN") for domain in DOMAINS}

    legacy = _latest_status(snap["statuses"], readiness.CONTEXT)
    governance = _latest_status(snap["statuses"], readiness.GOVERNANCE_CONTEXT)
    successor = readiness.evaluate(
        readiness.StaticReadinessObservation(
            draft=bool(snap["draft"]),
            mergeable=snap["mergeable"],
            # A Draft decision terminates before review authority is consulted.
            # Non-Draft semantic parity remains UNKNOWN until exact-head review
            # authority and review-thread facts are added to the snapshot.
            review_authority=("success", "shadow-placeholder"),
            check_runs=tuple(snap["checks"]),
            governance_status=governance,
        )
    )
    if snap["draft"] and legacy is not None:
        result["merge-readiness"] = {
            **base,
            "domain": "merge-readiness",
            "semantic_state": "COMPARED",
            "legacy_state": str(legacy.get("state") or ""),
            "successor_state": successor.state,
            "parity": str(legacy.get("state") or "") == successor.state,
        }
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repository", required=True)
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--generation", required=True, type=int)
    args = ap.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    snap = snapshot(args.repository, token, args.pr)
    canonical = json.dumps(snap, sort_keys=True, separators=(",", ":")).encode()
    projections = project(snap)
    record = {
        "schema": "hunter.governance-shadow-observation.v1",
        "authoritative": False,
        "generation": args.generation,
        "head_sha": snap["head_sha"],
        "input_digest": hashlib.sha256(canonical).hexdigest(),
        "domains": projections,
        "semantic_parity_claimed": any(item.get("semantic_state") == "COMPARED" for item in projections.values()),
        "all_domains_compared": all(item.get("semantic_state") == "COMPARED" for item in projections.values()),
    }
    workspace = Path(os.environ.get("GITHUB_WORKSPACE") or Path.cwd()).resolve()
    out = workspace / "shadow" / "observation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
