#!/usr/bin/env python3
"""Read-only governed next-Issue selection from authenticated GitHub REST facts.

The selected identity is advisory until the existing Issue-agent signed
authorization, admission, and #436 ingress accept it. No dispatch here.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hunter.automation.engineering_issue_queue import QueueAuthorityError
from hunter.automation.engineering_issue_queue_github import select_from_github

WORKFLOW = "hunter-issue-agent-trigger.yml"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise QueueAuthorityError("GitHub API redirect refused")


def _api(path: str, *, paginated: bool = False) -> Any:
    # No user-supplied endpoint is passed as a CLI argument. The only CLI call
    # retrieves the existing gh credential with a constant argv vector.
    token_result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False, timeout=10)
    if token_result.returncode or not token_result.stdout.strip():
        raise QueueAuthorityError("authenticated GitHub credential unavailable")
    if not path.startswith("repos/") or any(c in path for c in ("\r", "\n", "#", "@")):
        raise QueueAuthorityError("invalid GitHub REST path")
    url = "https://api.github.com/" + path
    pages: list[Any] = []
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(100):
        parsed_url = urlsplit(url)
        if parsed_url.scheme != "https" or parsed_url.netloc != "api.github.com":
            raise QueueAuthorityError("untrusted GitHub pagination URL")
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": "Bearer " + token_result.stdout.strip(),
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with opener.open(request, timeout=20) as response:
                data = json.load(response)
                link = response.headers.get("Link", "")
        except (urllib.error.URLError, ValueError, TimeoutError) as error:
            raise QueueAuthorityError("GitHub evidence unavailable") from error
        if not paginated:
            return data
        if not isinstance(data, list):
            raise QueueAuthorityError("invalid paginated GitHub response")
        pages.extend(data)
        next_links = [part.strip().split(";", 1)[0].strip("<>") for part in link.split(",") if 'rel="next"' in part]
        if len(next_links) > 1:
            raise QueueAuthorityError("ambiguous GitHub pagination")
        if not next_links:
            return pages
        url = next_links[0]
    raise QueueAuthorityError("GitHub pagination exceeds bound")


def read_selection(repository: str, owner_override: int | None = None) -> dict[str, Any]:
    repo = _api(f"repos/{repository}")
    if not isinstance(repo, Mapping) or repo.get("full_name") != repository:
        raise QueueAuthorityError("repository identity mismatch")
    owner = repo.get("owner")
    if not isinstance(owner, Mapping) or not isinstance(owner.get("login"), str):
        raise QueueAuthorityError("repository owner unavailable")
    owner_login = owner["login"]

    def list_issues() -> list[dict[str, Any]]:
        # REST issues endpoint includes PRs; filter them without dropping
        # Issue records or silently limiting the page count.
        records = _api(f"repos/{repository}/issues?state=open&per_page=100", paginated=True)
        return [record for record in records if "pull_request" not in record]

    def comments(n: int) -> list[dict[str, Any]]:
        return _api(f"repos/{repository}/issues/{n}/comments?per_page=100", paginated=True)

    def issue(n: int) -> dict[str, Any]:
        return _api(f"repos/{repository}/issues/{n}")

    def active() -> int:
        # Read the same workflow run authority as existing lifecycle code.
        data = _api(f"repos/{repository}/actions/workflows/{WORKFLOW}/runs?status=in_progress&per_page=1")
        if not isinstance(data, Mapping) or type(data.get("total_count")) is not int:
            raise QueueAuthorityError("indefinite active workflow count")
        return data["total_count"]

    def prs() -> list[int]:
        records = _api(f"repos/{repository}/pulls?state=open&per_page=100", paginated=True)
        # Unknown open branch ownership blocks the entire queue rather than
        # allowing a second concurrent engineering execution.
        owned = []
        for pr in records:
            head = pr.get("head")
            branch = head.get("ref") if isinstance(head, Mapping) else None
            if not isinstance(branch, str):
                raise QueueAuthorityError("indefinite open PR branch")
            if branch.startswith("issue-"):
                parts = branch.split("-", 2)
                if len(parts) < 3 or not parts[1].isdigit():
                    raise QueueAuthorityError("ambiguous active Issue PR branch")
                owned.append(int(parts[1]))
        return owned

    result = select_from_github(
        repository=repository,
        owner_login=owner_login,
        list_issues=list_issues,
        list_comments=comments,
        get_issue=issue,
        active_lifecycles=active,
        open_issue_agent_prs=prs,
        owner_override=owner_override,
    )
    return {
        "issue_number": result.issue_number,
        "policy_version": result.policy_version,
        "selection_digest": result.identity,
        "reason": result.reason,
        "evaluated": list(result.evaluated),
        "dispatch": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--owner-override", type=int)
    args = parser.parse_args()
    try:
        result = read_selection(args.repository, args.owner_override)
    except (QueueAuthorityError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"status": "OK", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
