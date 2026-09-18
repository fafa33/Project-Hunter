"""Trusted local PR reviewer adapter.

Candidate code is never executed. The adapter consumes the GitHub-rendered PR
diff and sends text only to a loopback Ollama endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODEL = "qwen2.5-coder:7b"
SCHEMA = "hunter.local-review.v1"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_review_prompt(diff: str) -> str:
    return (
        "Review this pull-request diff adversarially. Do not execute code. "
        "For Hunter governance/review code, explicitly test exact-head binding, authenticated review authority, "
        "retry and failover termination, acknowledgement vs review timeout semantics, provider exhaustion, "
        "and GitHub workflow permissions. Treat unbounded retry/failover, stale-head authority, missing authority, "
        "or permissions that cannot perform the declared action as blocking findings. Do not flag correct safeguards; "
        "only flag defective behavior actually present in the reviewed diff. "
        "Return JSON only with verdict ('clear' or 'findings'), summary, and "
        "findings [{severity,path,line,evidence}].\n\nDIFF:\n" + diff
    )


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Ollama response is not an object")
    return value


def ollama_review(diff: str, model: str = MODEL) -> dict[str, Any]:
    raw = post_json(
        OLLAMA_URL,
        {
            "model": model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "seed": 0},
            "prompt": build_review_prompt(diff),
        },
    )
    text = raw.get("response")
    if not isinstance(text, str):
        raise ValueError("Ollama response has no JSON review body")
    review = json.loads(text)
    if not isinstance(review, dict):
        raise ValueError("local review body is not an object")
    if review.get("verdict") not in {"clear", "findings"}:
        raise ValueError("local review verdict is invalid")
    if not isinstance(review.get("summary"), str) or not review["summary"].strip():
        raise ValueError("local review summary is missing")
    if not isinstance(review.get("findings"), list):
        raise ValueError("local review findings are malformed")
    return review


def build_result(
    *,
    head_sha: str,
    claims_id: str,
    model: str,
    findings: list[dict[str, Any]],
    summary: str,
    verdict: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Publish the model's own verdict, never a verdict inferred around it.

    A model that reports ``verdict: findings`` while emitting an empty findings
    list has produced an internally inconsistent review: one half says the
    candidate is blocked and the other half carries no blocker to act on.
    Deriving the published verdict from the findings list alone would silently
    resolve that contradiction in the permissive direction and publish a clear
    result for a review that declared itself blocking, so the inconsistency
    fails closed instead.
    """

    if len(head_sha) != 40 or len(claims_id) != 64:
        raise ValueError("exact-head or claims identity is malformed")
    derived = "findings" if findings else "clear"
    if verdict is None:
        verdict = derived
    if verdict not in {"clear", "findings"}:
        raise ValueError("local review verdict is invalid")
    if verdict != derived:
        raise ValueError(f"local review verdict {verdict!r} contradicts its own {len(findings)} findings")
    return {
        "schema": SCHEMA,
        "head_sha": head_sha,
        "claims_id": claims_id,
        "model": model,
        "model_digest": hashlib.sha256(model.encode()).hexdigest(),
        "verdict": verdict,
        "summary": summary,
        "findings": findings,
        "started_at": started_at or utc_now(),
        "completed_at": completed_at or utc_now(),
    }


def review_pr(
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    claims_id: str,
    fetch_diff: Callable[[str, int, str], str],
    review_diff: Callable[..., dict[str, Any]] = ollama_review,
    model: str = MODEL,
) -> dict[str, Any]:
    started = utc_now()
    diff = fetch_diff(repository, pr_number, head_sha)
    if not isinstance(diff, str) or not diff.strip():
        raise ValueError("pull-request diff is empty")
    review = review_diff(diff, model=model)
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise ValueError("review findings are malformed")
    return build_result(
        head_sha=head_sha,
        claims_id=claims_id,
        model=model,
        findings=findings,
        summary=str(review.get("summary") or "").strip(),
        verdict=review["verdict"] if "verdict" in review else None,
        started_at=started,
    )


def github_json(repository: str, token: str, path: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("GitHub object response is malformed")
    return value


def github_diff(repository: str, token: str, pr_number: int, head_sha: str) -> str:
    pr = github_json(repository, token, f"pulls/{pr_number}")
    if pr.get("state") != "open" or str((pr.get("head") or {}).get("sha") or "") != head_sha:
        raise ValueError("pull-request HEAD does not match requested exact head")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/pulls/{pr_number}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3.diff"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


#: `owner/repo`, in the character set GitHub actually allows for either part.
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\Z")
#: A full commit SHA. Abbreviations are refused: exact-head binding is the point.
HEAD_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
#: The claims digest carried by the review request this run is answering.
CLAIMS_ID_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _matching(pattern: re.Pattern[str], value: str, what: str) -> str:
    """Return `value` only when it is exactly what `what` is allowed to be."""
    candidate = value.strip()
    if pattern.fullmatch(candidate) is None:
        raise ValueError(f"{what} is malformed")
    return candidate


def resolved_output_path(value: str, workspace: Path | None = None) -> Path:
    """Resolve the result path, refusing anything outside the run's workspace.

    This runs on a self-hosted runner, so the file this writes is the one piece
    of the reviewer that can reach the host. The dispatch inputs are attacker
    reachable by definition -- whoever can trigger the workflow chooses them --
    so the destination is confined to the checkout that the run already owns,
    and `..` or an absolute path is refused rather than normalised away.
    """
    root = (workspace or Path.cwd()).resolve()
    resolved = (root / value).resolve()
    if root not in resolved.parents:
        raise ValueError("review output path escapes the workspace")
    if resolved.is_dir():
        raise ValueError("review output path is a directory")
    return resolved


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Hunter local exact-head reviewer")
    result.add_argument("--repository", required=True)
    result.add_argument("--pr", type=int, required=True)
    result.add_argument("--head-sha", required=True)
    result.add_argument("--claims-id", required=True)
    result.add_argument("--output", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    repository = _matching(REPOSITORY_PATTERN, args.repository, "repository")
    head_sha = _matching(HEAD_SHA_PATTERN, str(args.head_sha).lower(), "head SHA")
    claims_id = _matching(CLAIMS_ID_PATTERN, str(args.claims_id).lower(), "claims id")
    if args.pr <= 0:
        raise ValueError("pull-request number is malformed")
    output_path = resolved_output_path(args.output)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    result = review_pr(
        repository=repository,
        pr_number=args.pr,
        head_sha=head_sha,
        claims_id=claims_id,
        fetch_diff=lambda repo, pr, head: github_diff(repo, token, pr, head),
    )
    # Re-check exact HEAD after model execution so a stale result is never published.
    pr = github_json(repository, token, f"pulls/{args.pr}")
    if str((pr.get("head") or {}).get("sha") or "").lower() != head_sha:
        raise ValueError("pull-request HEAD changed during local review")
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
