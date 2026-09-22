from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import hunter_github_transport as transport

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


def project(snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    # Shadow v1 deliberately compares the canonical observable facts consumed by
    # both authorities. It does not claim semantic parity until successor domain
    # evaluators replace these identity projections.
    head = snap["head_sha"]
    base = {"head_sha": head, "draft": snap["draft"], "mergeable": snap["mergeable"]}
    return {domain: dict(base, domain=domain) for domain in DOMAINS}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repository", required=True)
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--generation", required=True, type=int)
    ap.add_argument("--output", required=True)
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
        "semantic_parity_claimed": False,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
