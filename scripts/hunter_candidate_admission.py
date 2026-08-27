from __future__ import annotations

import argparse
import os
from typing import Any

import hunter_github_transport as transport
import hunter_governance_review_v2 as governance

CONVERT_TO_DRAFT_MUTATION = """
mutation ConvertPullRequestToDraft($pullRequestId: ID!) {
  convertPullRequestToDraft(input: {pullRequestId: $pullRequestId}) {
    pullRequest {
      id
      isDraft
    }
  }
}
"""


def convert_to_draft(token: str, pull_request_node_id: str) -> None:
    if not pull_request_node_id:
        raise RuntimeError("Pull request node id is unavailable")
    payload: dict[str, Any] = transport.request_graphql_json(
        url="https://api.github.com/graphql",
        headers={},
        query=CONVERT_TO_DRAFT_MUTATION,
        variables={"pullRequestId": pull_request_node_id},
        token=token,
        what="convert unadmitted pull request to draft",
    )
    converted = payload.get("convertPullRequestToDraft") or {}
    pull_request = converted.get("pullRequest") or {}
    if pull_request.get("isDraft") is not True:
        raise RuntimeError("GitHub did not confirm pull request draft state")


def enforce_candidate_admission(repository: str, token: str, pr_number: int) -> int:
    pr = governance.read_mergeability(repository, token, pr_number)
    if pr.get("state") != "open":
        return 0

    base_ref = str((pr.get("base") or {}).get("ref") or "").strip()
    if base_ref != "main":
        return 0
    if pr.get("draft") is True:
        return 0

    head_sha = str((pr.get("head") or {}).get("sha") or "").strip()
    if not head_sha:
        raise RuntimeError(f"PR #{pr_number} head SHA is unavailable")

    admission_state, description = governance.candidate_admission(
        repository,
        token,
        head_sha,
    )
    if admission_state == "success":
        print(f"PR #{pr_number} admitted for review: {description}")
        return 0

    pull_request_node_id = str(pr.get("node_id") or "").strip()
    convert_to_draft(token, pull_request_node_id)
    print(
        f"PR #{pr_number} returned to Draft because exact-head candidate "
        f"admission is {admission_state}: {description}"
    )
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Keep unadmitted Project Hunter candidates out of review.")
    result.add_argument("--pr", type=int, required=True)
    result.add_argument("--repository", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        return enforce_candidate_admission(args.repository, token, args.pr)
    except transport.GitHubUnavailable as exc:
        print(f"Candidate admission infrastructure unavailable: {exc}")
        return 1
    except Exception as exc:
        print(f"Candidate admission failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
