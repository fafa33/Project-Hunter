"""Trusted default-branch publisher of verified reviewer-collector results.

The reviewer collector holds no status write: it cannot certify its own evidence. This small
publisher is the only thing that turns a collector run into a Hunter-specific result. It trusts
none of: a dispatch payload, a run title, or an artifact on its own. For one ``workflow_run`` event
it re-reads from GitHub

* the actual run (collector workflow, ``pull_request_target`` event, default branch, completed and
  successful, revision on default-branch history),
* that run's own integrity-checked receipt and the receipt's identity contract, and
* the current pull-request state (open, targets the default branch, still at the receipt's head,
  same claims, same remediation generation),

and only then publishes. The only thing it can publish is the existing non-blocking
``REVIEWER_UNAVAILABLE`` terminal state, and only when the receipt proves every enabled authority
reviewer gave no review. A run with no verifiable receipt publishes nothing: the cycle stays
pending (fail closed) and is recovered with GitHub's own re-run of the collector run, which raises
this same event again. There is no polling and no synthetic completion.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import hunter_pre_ready_review as pre_ready
import hunter_review_orchestrator as orchestration
import hunter_reviewer_collector as collector


def publish(repository: str, token: str, run_id: int) -> tuple[str, int | None]:
    """Verify one collector run and publish its result; returns ``(outcome, pull request or None)``.

    The pull request is returned only once the run, receipt and pull-request identity have all been
    verified, so a caller may refresh that pull request's governance status from it.
    """
    run, receipt = collector.verified_collector_receipt(repository, token, run_id)
    pr_number, head = int(receipt["pr_number"]), str(receipt["head_sha"])
    repo = orchestration.request_json(repository, token, "GET", "")
    branch = str((repo or {}).get("default_branch") or "")
    pr = orchestration.request_json(repository, token, "GET", f"pulls/{pr_number}")
    if not branch or not isinstance(pr, dict):
        raise ValueError("pull request state is unavailable")
    if pr.get("state") != "open" or (pr.get("base") or {}).get("ref") != branch:
        return "SKIPPED: pull request is not open against the default branch", None
    if (pr.get("head") or {}).get("sha") != head:
        return "SKIPPED: the collected head is no longer the pull-request head", None
    readiness = orchestration.review_request_state(repository, token, pr_number, head)
    if not readiness.ready:
        return f"SKIPPED: review request not usable ({readiness.reason})", pr_number
    generation = orchestration.current_remediation_generation(repository, token, pr_number, head, readiness.claims_id)
    pool, pool_error = pre_ready.load_reviewer_pool()
    if pool is None or pool_error:
        raise ValueError(f"reviewer pool unavailable: {pool_error}")
    verdict = orchestration.pool_exhaustion(
        receipt,
        repository=repository,
        pr_number=pr_number,
        head_sha=head,
        run_id=run_id,
        claims_id=readiness.claims_id,
        generation_id=generation,
        pool=pool,
    )
    if not verdict.exhausted:
        # Not an unavailable pool: a reviewer answered, or the receipt is stale for this head's
        # claims/generation. Review authority stays with the authenticated-review path.
        return f"NO TERMINAL RESULT: {verdict.reason}", pr_number
    digest = orchestration.reviewer_pool_config_digest()
    state, existing, _error = orchestration.read_cycle(repository, token, pr_number, head)
    if (
        state == "present"
        and existing is not None
        and existing.config_digest == digest
        and existing.generation_id == generation
        and existing.state not in orchestration.OPEN_CYCLE_STATES
    ):
        return f"SKIPPED: the cycle is already {existing.state}", pr_number
    orchestration.publish_cycle(
        repository,
        token,
        head,
        cycle=orchestration.ReviewCycle(
            pr_number=pr_number,
            head_sha=head,
            state="REVIEWER_UNAVAILABLE",
            provider_id="",
            trigger_id=int(run["id"]),
            started_at="",
            config_digest=digest,
            generation_id=generation,
        ),
    )
    return "PUBLISHED: REVIEWER_UNAVAILABLE", pr_number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    outcome, pr_number = publish(args.repository, token, args.run_id)
    print(outcome)
    output = os.environ.get("GITHUB_OUTPUT")
    if output and pr_number is not None:
        # Written only after the run, receipt and pull request were all verified.
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"pr_number={pr_number}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
