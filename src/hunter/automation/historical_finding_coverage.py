"""Read-only, fail-closed accounting for historical findings in the anchored knowledge ledger.

Historical GitHub resolution or merge is *not* equivalent to signed exact-head proof.
This projection never writes ledger records or promotes a family.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from hunter.automation.issue_agent_knowledge import KnowledgeView

# Reviewed, bounded retrospective mapping: exact historical GitHub identities only.
# This is evidence attribution, never a RED-to-GREEN or exact-head Issue-agent proof.
HISTORICAL_EVIDENCE: dict[int, dict[str, Any]] = {
    579: {
        "final_head": "3beac11ee17966e369e5875928c924ed3edae6ef",
        "merge_commit": "cbfa4280a840c6f289caed7f34df7cca6eae3cc0",
        "preflight_job_id": 113502967154,
        "findings": {
            4222875872: {
                "reviewed_head": "7bc7e3d1cdb6adddf44fcb26b77b3eb05e9ffcc9",
                "commit": "2fd21e432cc1f2753ea16590b80b80eed790bd63",
                "paths": ("src/hunter/automation/issue_agent_authorize.py",),
                "tests": (
                    "tests/test_issue_agent_authorize.py::test_prepare_reads_configured_checkout_registry_not_ambient_cwd",
                ),
                "disposition": "historical-fix-evidence",
            },
            4222896715: {
                "reviewed_head": "7bc7e3d1cdb6adddf44fcb26b77b3eb05e9ffcc9",
                "commit": "2fd21e432cc1f2753ea16590b80b80eed790bd63",
                "paths": ("src/hunter/automation/issue_agent_authorize.py",),
                "tests": (
                    "tests/test_issue_agent_authorize.py::test_prepare_reads_configured_checkout_registry_not_ambient_cwd",
                ),
                "disposition": "historical-fix-evidence",
            },
            4222896794: {
                "reviewed_head": "7bc7e3d1cdb6adddf44fcb26b77b3eb05e9ffcc9",
                "commit": "2fd21e432cc1f2753ea16590b80b80eed790bd63",
                "paths": ("tests/test_issue_agent_authorize.py",),
                "tests": (
                    "tests/test_issue_agent_authorize.py::test_prepare_reads_configured_checkout_registry_not_ambient_cwd",
                ),
                "disposition": "historical-fix-evidence",
            },
        },
    },
    581: {
        "final_head": "1196534b30ee1ac3ace1ba3530b77b9691ffdaf8",
        "merge_commit": "207e28b8074dc3c783529da215d78a54b6d4d78d",
        "preflight_job_id": 113553726043,
        "findings": {
            4223707384: {
                "reviewed_head": "94f9e748adfefcbaafc6bc58345f825245f5a485",
                "commit": "cfa0dd7ebd713a535ba6daa5cd3fcb51bd51cb59",
                "paths": ("tests/test_hunter_ci_impact.py",),
                "tests": ("tests/test_hunter_ci_impact.py::test_cli_focused_executes_and_propagates_failure",),
                "disposition": "historical-fix-evidence",
            },
            4224365171: {
                "reviewed_head": "61dea1409f5fe4b3ef804150c2a6a27f7e1b7610",
                "commit": "1196534b30ee1ac3ace1ba3530b77b9691ffdaf8",
                "paths": ("scripts/hunter_pr_preflight.py",),
                "tests": ("tests/test_hunter_pr_preflight.py::test_expected_red_lane_timing_includes_pytest_duration",),
                "disposition": "historical-regression-gap",
            },
        },
    },
    582: {
        "final_head": "382032bebcd4c5c04b4940be98c6bd9a9bbb18aa",
        "merge_commit": "d1130e539592a8e1fa2b2c5b220a2ce3b1ab4914",
        "preflight_job_id": 113596464539,
        "findings": {
            4224886962: {
                "reviewed_head": "f750bb0f8d29890d3dcbf7ee716711f317be5fb9",
                "commit": "26d0ebe538a5fcf99d0b864464e8c16d654df591",
                "paths": ("scripts/hunter_preflight_reuse_decision.py",),
                "tests": ("tests/test_preflight_reuse_decision.py::test_changed_pr_head_refuses_reuse",),
                "disposition": "historical-fix-evidence",
            },
            4224887008: {
                "reviewed_head": "f750bb0f8d29890d3dcbf7ee716711f317be5fb9",
                "commit": "26d0ebe538a5fcf99d0b864464e8c16d654df591",
                "paths": ("scripts/hunter_governance_review_v2.py",),
                "tests": ("tests/test_preflight_reuse_decision.py::test_malformed_pr_file_listing_refuses_reuse",),
                "disposition": "historical-fix-evidence",
            },
            4225085464: {
                "reviewed_head": "26d0ebe538a5fcf99d0b864464e8c16d654df591",
                "commit": "8be7836a63e154e418e9fc6945ba0f270672c076",
                "paths": ("scripts/hunter_preflight_reuse_decision.py",),
                "tests": (
                    "tests/test_preflight_reuse_decision.py::test_candidate_controlled_bootstrap_forces_trusted_full_gates",
                ),
                "disposition": "historical-fix-evidence",
            },
        },
    },
}


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
    # The GitHub inventory is independent of the signed ledger.  A partial
    # ingestion must not silently turn live unresolved findings into zero gaps.
    anchored = {
        (finding.pull_request_number, finding.ingested["provenance"]["comment_id"])
        for finding in view.findings.values()
        if finding.pull_request_number in selected
    }
    for key, thread in github_threads.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or any(not isinstance(part, int) or isinstance(part, bool) or part <= 0 for part in key)
            or not isinstance(thread, Mapping)
            or thread.get("pull_request_number") != key[0]
            or thread.get("comment_id") != key[1]
        ):
            raise ValueError("GitHub thread evidence identity mismatch")
        if not isinstance(thread.get("resolved"), bool):
            raise ValueError("GitHub thread resolution must be a boolean")
        if key[0] in selected and key not in anchored:
            raise ValueError("GitHub thread missing from signed ledger")
    rows: list[dict[str, Any]] = []
    for identity, finding in sorted(view.findings.items()):
        pr = finding.pull_request_number
        if pr not in selected:
            continue
        comment = finding.ingested["provenance"]["comment_id"]
        thread = github_threads.get((pr, comment))
        if finding.resolved is not None:
            status = "ledger-resolved" if finding.proven is not None else "invalid-ledger-state"
        elif finding.proven is not None:
            status = "proven-awaiting-ledger-resolution"
        elif thread is None:
            status = "source-unavailable"
        elif thread.get("comment_id") != comment or thread.get("pull_request_number") != pr:
            raise ValueError("GitHub thread evidence identity mismatch")
        elif not isinstance(thread.get("resolved"), bool):
            raise ValueError("GitHub thread resolution must be a boolean")
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
                "historical_assessed": finding.historical_assessment is not None,
                "historical_disposition": (finding.historical_assessment or {}).get("disposition"),
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
        "historical_assessed": sum(row["historical_assessed"] for row in rows),
        "proof_gap": unverified,
        "counts": counts,
        "findings": rows,
    }
