#!/usr/bin/env python3
"""Trusted Draft PR opener for governed Issue Agent candidates.

``docs/ISSUE_AGENT_EXECUTION_CONTRACT.md`` I6. After a Railway execution pushes
a candidate to its own ``issue-<n>-<16 hex>`` branch and ``Hunter / Pre-PR
Preflight`` passes on that exact head, the workflow
``.github/workflows/hunter-issue-agent-candidate-pr.yml`` runs this module from
the trusted default branch. It opens one Draft pull request against ``main``,
and only when every one of these holds:

- the branch has the exact governed agent-branch shape and binds Issue ``<n>``;
- the preflight run's head SHA is still the branch head;
- Issue ``<n>`` exists, is open and is an Issue rather than a pull request;
- no pull request is already open for that branch;
- the commit range from ``main`` is complete, non-empty, has no merge commit
  and ends at the head;
- every commit in it carries a verified signature from an authorized signer in
  the trusted ``docs/CODE_WRITE_POLICY.json``.

A Draft PR creates no authority. It only makes the candidate visible to the
existing Candidate Admission, Hunter Governance Review, Pre-Ready and Merge
Readiness chain, which re-verify everything independently. This module never
marks a PR ready, never approves and never merges.

The PR is created with ``HUNTER_ISSUE_AGENT_PR_TOKEN``, never the workflow
``GITHUB_TOKEN``: GitHub starts no ``pull_request`` workflows for events that
``GITHUB_TOKEN`` creates, so such a PR would never receive its required checks.
A missing token fails closed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

AGENT_BRANCH_RE = re.compile(r"issue-([1-9][0-9]{0,9})-([0-9a-f]{16})")
BASE_BRANCH = "main"
PR_TOKEN_ENV = "HUNTER_ISSUE_AGENT_PR_TOKEN"
READ_TOKEN_ENV = "GITHUB_TOKEN"
_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_TITLE_LIMIT = 200

#: ``(repository, token, method, path, payload) -> decoded JSON``
RequestJson = Callable[[str, str, str, str, "dict[str, Any] | None"], Any]


@dataclass(frozen=True)
class CandidateEvidence:
    """Trusted GitHub evidence about one pushed candidate branch."""

    branch: str
    workflow_head_sha: str
    branch_head_sha: str | None
    issue: Mapping[str, Any] | None
    open_pull_requests: tuple[Mapping[str, Any], ...]
    range_commits: tuple[Mapping[str, Any], ...]
    authorized_signers: frozenset[str]


@dataclass(frozen=True)
class CandidatePrDecision:
    """Whether to open the Draft PR, and exactly which one."""

    open: bool
    reason: str
    head: str = ""
    base: str = BASE_BRANCH
    draft: bool = True
    title: str = ""
    body: str = ""
    issue_number: int | None = None


def governed_issue_number(branch: str) -> int | None:
    """The Issue a governed agent branch binds, or ``None`` for any other branch."""
    match = AGENT_BRANCH_RE.fullmatch(branch)
    return int(match.group(1)) if match is not None else None


def _refuse(reason: str, issue_number: int | None = None) -> CandidatePrDecision:
    return CandidatePrDecision(open=False, reason=reason, issue_number=issue_number)


def _signer(entry: Mapping[str, Any]) -> str:
    committer = entry.get("committer")
    login = committer.get("login") if isinstance(committer, Mapping) else None
    return str(login or "").strip().lower()


def _parents(entry: Mapping[str, Any]) -> list[str]:
    parents = entry.get("parents")
    if not isinstance(parents, list):
        return []
    return [str(parent.get("sha") or "") for parent in parents if isinstance(parent, Mapping)]


def _pr_body(issue_number: int, branch: str, head: str) -> str:
    return "\n".join(
        (
            "## Summary",
            "",
            f"Governed Issue Agent candidate for #{issue_number}, pushed to `{branch}` at `{head}`.",
            "",
            "## Scope / architecture impact",
            "",
            f"- Governing Issue or ADR, when applicable: #{issue_number}",
            "- Architecture impact: see the candidate diff",
            "- Evidence / persistence / replay impact: see the candidate diff",
            "",
            "## Verification",
            "",
            "Opened by `Hunter / Issue Agent Candidate PR` after `Hunter / Pre-PR Preflight` passed on the exact",
            "head. Candidate Admission, Hunter Governance Review, Pre-Ready review and Hunter Merge Readiness",
            "remain the only admission and merge authorities (`docs/ISSUE_AGENT_EXECUTION_CONTRACT.md`).",
            "",
            "## Review disposition",
            "",
            "Pending independent review.",
            "",
            "## Operational notes",
            "",
            "This Draft PR grants no authority. It is never marked ready or merged automatically; owner approval",
            "is required.",
        )
    )


def decide_candidate_pr(evidence: CandidateEvidence) -> CandidatePrDecision:
    """Decide from trusted evidence whether one Draft PR may be opened."""
    issue_number = governed_issue_number(evidence.branch)
    if issue_number is None:
        return _refuse(f"branch {evidence.branch!r} is not a governed Issue Agent branch")

    head = evidence.workflow_head_sha.strip().lower()
    if _COMMIT_SHA_RE.fullmatch(head) is None:
        return _refuse("preflight head is not an exact commit SHA", issue_number)
    if (evidence.branch_head_sha or "").strip().lower() != head:
        return _refuse("the preflight head is no longer the branch head", issue_number)

    issue = evidence.issue
    if not isinstance(issue, Mapping) or issue.get("number") != issue_number:
        return _refuse(f"governing Issue #{issue_number} does not exist", issue_number)
    if issue.get("pull_request") is not None:
        return _refuse(f"#{issue_number} is a pull request, not a governing Issue", issue_number)
    if issue.get("state") != "open":
        return _refuse(f"governing Issue #{issue_number} is not open", issue_number)

    if evidence.open_pull_requests:
        return _refuse("a pull request is already open for this branch", issue_number)

    commits = evidence.range_commits
    if not commits:
        return _refuse("the branch carries no candidate commits beyond main", issue_number)
    if str(commits[-1].get("sha") or "").lower() != head:
        return _refuse("the commit range evidence does not end at the head", issue_number)
    for entry in commits:
        sha = str(entry.get("sha") or "").lower()
        if len(_parents(entry)) != 1:
            return _refuse(
                f"commit {sha[:10]} is a merge commit; an authorization branch is never merged or rebased",
                issue_number,
            )
        verification = (entry.get("commit") or {}).get("verification")
        if not isinstance(verification, Mapping) or verification.get("verified") is not True:
            return _refuse(f"commit {sha[:10]} has no verified signature", issue_number)
        if verification.get("reason") != "valid":
            return _refuse(f"commit {sha[:10]} signature is not valid", issue_number)
        signer = _signer(entry)
        if signer not in evidence.authorized_signers:
            return _refuse(f"commit {sha[:10]} was written by unauthorized signer {signer or 'unknown'}", issue_number)

    issue_title = str(issue.get("title") or "").strip()
    title = f"Issue Agent candidate for #{issue_number}: {issue_title}".strip()
    return CandidatePrDecision(
        open=True,
        reason="governed Issue Agent candidate is eligible for a Draft PR",
        head=evidence.branch,
        base=BASE_BRANCH,
        draft=True,
        title=title[:_TITLE_LIMIT],
        body=_pr_body(issue_number, evidence.branch, head),
        issue_number=issue_number,
    )


def _default_request_json() -> RequestJson:
    import hunter_github_transport as transport

    def request(repository: str, token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        return transport.request_rest_json(
            url=f"https://api.github.com/repos/{repository}/{path}",
            method=method,
            headers={},
            data=data,
            token=token,
            what=f"{method} {path}",
        )

    return request


def _is_not_found(error: BaseException) -> bool:
    return getattr(error, "status_code", None) == 404


def _authorized_signers() -> frozenset[str]:
    import hunter_governance_review_v2 as governance

    signers, _floor, error = governance.load_ingress_provenance_policy()
    if error is not None:
        raise RuntimeError(error)
    return frozenset(signers)


def gather_evidence(
    *,
    repository: str,
    branch: str,
    head_sha: str,
    token: str,
    request_json: RequestJson,
    authorized_signers: frozenset[str],
) -> CandidateEvidence:
    """Read the trusted evidence for one agent branch from GitHub."""
    issue_number = governed_issue_number(branch)
    assert issue_number is not None
    owner = repository.split("/", 1)[0]
    try:
        ref = request_json(repository, token, "GET", f"git/ref/heads/{quote(branch, safe='')}", None)
        branch_head = str(((ref or {}).get("object") or {}).get("sha") or "")
    except Exception as error:  # noqa: BLE001 - a deleted branch is simply not the head
        if not _is_not_found(error):
            raise
        branch_head = ""
    try:
        issue = request_json(repository, token, "GET", f"issues/{issue_number}", None)
    except Exception as error:  # noqa: BLE001
        if not _is_not_found(error):
            raise
        issue = None
    pulls = request_json(
        repository, token, "GET", f"pulls?state=open&head={quote(owner + ':' + branch, safe='')}&per_page=100", None
    )
    compare = request_json(repository, token, "GET", f"compare/{BASE_BRANCH}...{head_sha}?per_page=250", None)
    commits = compare.get("commits") if isinstance(compare, Mapping) else None
    total = compare.get("total_commits") if isinstance(compare, Mapping) else None
    if not isinstance(commits, list) or total != len(commits):
        # A truncated range is not evidence that the omitted commits are signed.
        raise RuntimeError("the candidate commit range evidence is incomplete")
    return CandidateEvidence(
        branch=branch,
        workflow_head_sha=head_sha,
        branch_head_sha=branch_head,
        issue=issue if isinstance(issue, Mapping) else None,
        open_pull_requests=tuple(pull for pull in (pulls or []) if isinstance(pull, Mapping)),
        range_commits=tuple(entry for entry in commits if isinstance(entry, Mapping)),
        authorized_signers=authorized_signers,
    )


def run(
    *,
    repository: str,
    branch: str,
    head_sha: str,
    environ: Mapping[str, str],
    request_json: RequestJson | None = None,
    authorized_signers: frozenset[str] | None = None,
) -> tuple[int, CandidatePrDecision]:
    """Evaluate one pushed branch and open its Draft PR when eligible."""
    if governed_issue_number(branch) is None:
        return 0, _refuse(f"branch {branch!r} is not a governed Issue Agent branch; nothing to open")
    pr_token = environ.get(PR_TOKEN_ENV, "").strip()
    if not pr_token:
        return 2, _refuse(
            f"{PR_TOKEN_ENV} is not configured; a GITHUB_TOKEN-created PR would never run its required checks"
        )
    read_token = environ.get(READ_TOKEN_ENV, "").strip() or pr_token
    request = request_json or _default_request_json()
    evidence = gather_evidence(
        repository=repository,
        branch=branch,
        head_sha=head_sha,
        token=read_token,
        request_json=request,
        authorized_signers=authorized_signers if authorized_signers is not None else _authorized_signers(),
    )
    decision = decide_candidate_pr(evidence)
    if not decision.open:
        return 0, decision
    request(
        repository,
        pr_token,
        "POST",
        "pulls",
        {
            "title": decision.title,
            "head": decision.head,
            "base": decision.base,
            "body": decision.body,
            "draft": True,
            "maintainer_can_modify": False,
        },
    )
    return 0, decision


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hunter_issue_agent_candidate_pr")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--head-sha", required=True)
    arguments = parser.parse_args(argv)
    try:
        code, decision = run(
            repository=arguments.repository,
            branch=arguments.branch,
            head_sha=arguments.head_sha,
            environ=os.environ,
        )
    except Exception as error:  # noqa: BLE001 - fail closed with the reason
        print(f"::error::Issue Agent candidate PR failed closed: {type(error).__name__}: {error}")
        return 2
    payload = {key: value for key, value in asdict(decision).items() if key != "body"}
    print(json.dumps(payload, sort_keys=True))
    if code != 0:
        print(f"::error::{decision.reason}")
    return code


if __name__ == "__main__":
    sys.exit(main())
