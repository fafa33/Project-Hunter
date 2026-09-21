"""Trusted exact-head Hermes review adapter.

Candidate code is never executed. The adapter fetches the GitHub-rendered diff,
passes text only to Hermes in safe CLI mode, and publishes the same immutable
review schema used by other trusted local workflow reviewers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import hunter_local_reviewer as local

MODEL = "hermes-configured-default"


def _json_from_stdout(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Hermes produced no JSON review object")


def hermes_binary() -> str:
    found = shutil.which("hermes")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "hermes"
    if fallback.is_file():
        return str(fallback)
    raise ValueError("Hermes CLI is unavailable on the reviewer runner")


def hermes_review(diff: str, model: str = MODEL) -> dict[str, Any]:
    prompt = local.build_review_prompt(diff)
    command = [hermes_binary(), "--safe-mode", "--cli", "-z", prompt]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=300, check=False)
    if completed.returncode != 0:
        raise ValueError(f"Hermes review execution failed with exit code {completed.returncode}")
    review = _json_from_stdout(completed.stdout)
    if review.get("verdict") not in {"clear", "findings"}:
        raise ValueError("Hermes review verdict is invalid")
    if not isinstance(review.get("summary"), str) or not review["summary"].strip():
        raise ValueError("Hermes review summary is missing")
    if not isinstance(review.get("findings"), list):
        raise ValueError("Hermes review findings are malformed")
    return review


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Hunter Hermes exact-head reviewer")
    ap.add_argument("--repository", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--claims-id", required=True)
    ap.add_argument("--output", required=True)
    return ap


def main() -> int:
    args = parser().parse_args()
    repository = local._matching(local.REPOSITORY_PATTERN, args.repository, "repository")
    head_sha = local._matching(local.HEAD_SHA_PATTERN, str(args.head_sha).lower(), "head SHA")
    claims_id = local._matching(local.CLAIMS_ID_PATTERN, str(args.claims_id).lower(), "claims id")
    if args.pr <= 0:
        raise ValueError("pull-request number is malformed")
    output_path = local.resolved_output_path(args.output)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    result = local.review_pr(
        repository=repository,
        pr_number=args.pr,
        head_sha=head_sha,
        claims_id=claims_id,
        fetch_diff=lambda repo, pr, head: local.github_diff(repo, token, pr, head),
        review_diff=hermes_review,
        model=MODEL,
    )
    pr = local.github_json(repository, token, f"pulls/{args.pr}")
    if str((pr.get("head") or {}).get("sha") or "").lower() != head_sha:
        raise ValueError("pull-request HEAD changed during Hermes review")
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
