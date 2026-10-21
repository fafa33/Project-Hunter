"""Read-only, fail-closed accounting for historical findings in the anchored knowledge ledger.

Historical GitHub resolution or merge is *not* equivalent to signed exact-head proof.
This projection never writes ledger records or promotes a family.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from hunter.automation.issue_agent_knowledge import KnowledgeView


def coverage(
    view: KnowledgeView,
    *,
    pull_requests: Iterable[int],
    github_threads: Mapping[tuple[int, int], Mapping[str, Any]],
) -> dict[str, Any]:
    """Account for each anchored finding and refuse unknown/malformed external thread evidence.

    ``github_threads`` must be keyed by (PR number, GitHub comment database ID).
    Its data is advisory only; the signed knowledge view remains authoritative.
    """
    selected = set(pull_requests)
    if not selected or any(not isinstance(pr, int) or isinstance(pr, bool) or pr <= 0 for pr in selected):
        raise ValueError("nonempty positive PR selection required")
    rows: list[dict[str, Any]] = []
    for identity, finding in sorted(view.findings.items()):
        pr = finding.pull_request_number
        if pr not in selected:
            continue
        comment = finding.ingested["provenance"]["comment_id"]
        thread = github_threads.get((pr, comment))
        if thread is None:
            status = "source-unavailable"
        elif thread.get("comment_id") != comment or thread.get("pull_request_number") != pr:
            raise ValueError("GitHub thread evidence identity mismatch")
        elif not isinstance(thread.get("resolved"), bool):
            raise ValueError("GitHub thread resolution must be a boolean")
        elif finding.resolved is not None:
            status = "ledger-resolved" if finding.proven is not None else "invalid-ledger-state"
        elif finding.proven is not None:
            status = "proven-awaiting-ledger-resolution"
        elif thread["resolved"]:
            status = "github-resolved-without-signed-proof"
        else:
            status = "unproven-open"
        rows.append(
            {
                "finding_id": identity,
                "pull_request_number": pr,
                "comment_id": comment,
                "status": status,
                "signed_proof": finding.proven is not None,
                "signed_resolution": finding.resolved is not None,
                "regression_tests": list(finding.proven["regression_tests"]) if finding.proven else [],
            }
        )
    counts = {status: sum(row["status"] == status for row in rows) for status in sorted({r["status"] for r in rows})}
    unverified = sum(not row["signed_proof"] for row in rows)
    return {
        "schema_version": "hunter-historical-finding-coverage-v1",
        "pull_requests": sorted(selected),
        "examined": len(rows),
        "signed_proven": len(rows) - unverified,
        "proof_gap": unverified,
        "counts": counts,
        "findings": rows,
    }
