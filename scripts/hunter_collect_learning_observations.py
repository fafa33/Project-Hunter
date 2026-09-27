#!/usr/bin/env python3
"""Collect exact-head GitHub review observations without assigning defect authority."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import hunter_governance_review_v2 as governance

from hunter.evidence_intelligence.historical_evidence_adjudication import split_owner_disposition


def _normalized_login(value: str) -> str:
    login = value.strip().lower()
    return login[:-5] if login.endswith("[bot]") else login


def _trusted_reviewer_logins() -> frozenset[str]:
    import hunter_pre_ready_review as pre_ready

    pool, error = pre_ready.load_reviewer_pool()
    if pool is None or error:
        raise ValueError(f"reviewer pool unavailable: {error}")
    return frozenset(
        _normalized_login(str(agent.get("github_login") or ""))
        for agent in pre_ready.authority_pool_reviewers(pool)
        if str(agent.get("github_login") or "").strip()
    )


_FAMILY = __import__("re").compile(r"\[family:(DFF-[0-9]{3})\]", __import__("re").I)
_TEST = __import__("re").compile(r"\[test:([^\]]+::[^\]]+)\]", __import__("re").I)


def _structured_owner_disposition(body: str) -> tuple[str, str, str, str | None, list[str]] | None:
    classification, substance, fix_reference = split_owner_disposition(body)
    if classification is None:
        return None
    family_match = _FAMILY.search(substance)
    test_matches = _TEST.findall(substance)
    family = family_match.group(1).upper() if family_match else None
    clean = _FAMILY.sub(" ", _TEST.sub(" ", substance))
    clean = " ".join(clean.split())
    return classification, clean, fix_reference, family, test_matches


def _review_threads(repository: str, token: str, pr: int) -> dict[int, bool]:
    owner, name = repository.split("/", 1)
    query = """
    query($owner: String!, $name: String!, $number: Int!, $after: String) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          reviewThreads(first: 100, after: $after) {
            nodes { isResolved comments(first: 1) { nodes { databaseId } } }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }
    """
    result: dict[int, bool] = {}
    cursor: str | None = None
    while True:
        data = governance.transport.request_graphql_json(
            url="https://api.github.com/graphql",
            headers={},
            token=token,
            query=query,
            variables={"owner": owner, "name": name, "number": pr, "after": cursor},
            what="learning review threads",
        )
        page = data["repository"]["pullRequest"]["reviewThreads"]
        nodes = page.get("nodes")
        info = page.get("pageInfo")
        if not isinstance(nodes, list) or not isinstance(info, dict) or not isinstance(info.get("hasNextPage"), bool):
            raise ValueError("review thread payload is malformed")
        for node in nodes:
            comments = (node.get("comments") or {}).get("nodes") if isinstance(node, dict) else None
            if (
                not isinstance(node, dict)
                or not isinstance(node.get("isResolved"), bool)
                or not isinstance(comments, list)
                or not comments
            ):
                raise ValueError("review thread evidence is malformed")
            comment_id = comments[0].get("databaseId") if isinstance(comments[0], dict) else None
            if not isinstance(comment_id, int):
                raise ValueError("review thread opening comment id is malformed")
            result[comment_id] = node["isResolved"]
        if not info["hasNextPage"]:
            return result
        cursor = info.get("endCursor")
        if not isinstance(cursor, str) or not cursor:
            raise ValueError("review thread pagination cursor is malformed")


def _owner_replies(
    comments: list[dict[str, Any]], owner_login: str
) -> dict[int, tuple[str, str, str, str | None, list[str]]]:
    replies: dict[int, tuple[str, str, str, str | None, list[str]]] = {}
    for item in comments:
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        target = item.get("in_reply_to_id")
        if str(user.get("login") or "") != owner_login or not isinstance(target, int):
            continue
        parsed = _structured_owner_disposition(str(item.get("body") or ""))
        if parsed is not None:
            replies[target] = parsed
    return replies


def collect(repository: str, token: str, pr: int, head: str, base: str) -> list[dict[str, Any]]:
    """Collect the durable review history for *pr*, including remediated older heads.

    A review comment is never promoted to confirmed knowledge merely because a
    bot wrote it or a thread was clicked Resolved.  Confirmation/exclusion is
    derived only from an explicit repository-owner disposition on that exact
    thread.  Resolution is additionally required for a confirmed finding, so a
    half-remediated thread cannot enter canonical learning.  Neutral findings
    remain durable observations instead of disappearing when HEAD moves.
    """
    comments: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = governance.request_json(repository, token, "GET", f"pulls/{pr}/comments?per_page=100&page={page}")
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise ValueError("review comments payload must be a list of objects")
        comments.extend(payload)
        if len(payload) < 100:
            break
        page += 1

    owner = repository.split("/", 1)[0]

    # The webhook payload is immutable evidence for the event that woke this
    # collector. Current-state REST reconstruction alone is insufficient: a
    # trusted review comment may already have been deleted by the time a queued
    # job runs. Merge the authenticated event comment back into the scan before
    # disposition capture so created/deleted races cannot silently erase it.
    #
    # A deleted trusted-reviewer finding must be recovered this way (it is
    # never a reply, so it can only ever become a durable neutral observation
    # below -- never a disposition). A deleted OWNER *disposition reply*,
    # however, must never be revived as a live disposition: recovering it
    # into `comments` would let `_owner_replies()` parse a retracted
    # confirmation and, on an already-resolved thread, incorrectly promote a
    # finding to `confirmed`. The distinction uses the authenticated event's
    # own action plus the comment's author/reply-relationship identity, not
    # any inference from current REST state.
    event_path = os.environ.get("GITHUB_EVENT_PATH") or ""
    if event_path:
        try:
            with open(event_path, encoding="utf-8") as handle:
                event = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("GitHub event payload is unreadable") from exc
        event_repo = (event.get("repository") or {}).get("full_name") if isinstance(event, dict) else None
        event_pr = (event.get("pull_request") or {}).get("number") if isinstance(event, dict) else None
        event_action = event.get("action") if isinstance(event, dict) else None
        event_comment = event.get("comment") if isinstance(event, dict) else None
        if event_comment is not None and event_repo == repository and event_pr == pr:
            if not isinstance(event_comment, dict) or not isinstance(event_comment.get("id"), int):
                raise ValueError("GitHub review-comment event evidence is malformed")
            if not any(item.get("id") == event_comment["id"] for item in comments):
                event_user = event_comment.get("user") if isinstance(event_comment.get("user"), dict) else {}
                event_login = str(event_user.get("login") or "")
                is_deleted_owner_disposition_reply = (
                    event_action == "deleted"
                    and event_comment.get("in_reply_to_id") is not None
                    and event_login == owner
                )
                if not is_deleted_owner_disposition_reply:
                    comments.append(event_comment)

    trusted_reviewers = _trusted_reviewer_logins()
    dispositions = _owner_replies(comments, owner)
    thread_state = _review_threads(repository, token, pr) if dispositions else {}
    observations: list[dict[str, Any]] = []
    for item in comments:
        if item.get("in_reply_to_id") is not None:
            continue
        comment_id = item.get("id")
        if not isinstance(comment_id, int):
            raise ValueError("review comment id must be an integer")
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        reviewer = str(user.get("login") or "")
        if _normalized_login(reviewer) not in trusted_reviewers:
            continue
        classification = None
        invariant = None
        fix_reference = None
        affected_paths: list[str] = []
        regression_evidence: list[str] = []
        claimed_family_id: str | None = None
        disposition = dispositions.get(comment_id)
        if disposition is not None:
            disposition_class, disposition_invariant, disposition_fix, disposition_family, disposition_tests = (
                disposition
            )
            # A lifecycle disposition is terminal only when the exact thread is resolved.
            # Confirmed knowledge additionally requires the complete replayable evidence contract.
            if thread_state.get(comment_id) is True:
                if disposition_class != "confirmed":
                    classification = disposition_class
                elif (
                    disposition_fix
                    and disposition_family
                    and disposition_tests
                    and disposition_invariant
                    and item.get("path")
                ):
                    classification = "confirmed"
                    invariant = disposition_invariant
                    fix_reference = disposition_fix
                    affected_paths = [str(item["path"])]
                    regression_evidence = disposition_tests
                    claimed_family_id = disposition_family
        observations.append(
            {
                "source": "github-review",
                "provider": "github-review",
                "event_id": f"review-comment-{comment_id}",
                "source_pr": pr,
                "reviewed_head_sha": head,
                "reviewed_base_sha": base,
                "source_event_head_sha": item.get("commit_id") or item.get("original_commit_id"),
                "reviewer": reviewer or "github-review",
                "path": item.get("path"),
                "line": item.get("line") or item.get("original_line"),
                "message": str(item.get("body") or "review finding"),
                "availability": "available",
                "classification": classification,
                "invariant": invariant,
                "affected_paths": affected_paths,
                "fix_reference": fix_reference,
                "regression_evidence": regression_evidence,
                "claimed_family_id": claimed_family_id,
            }
        )

    # Top-level reviews are durable evidence, but cannot become canonical
    # defect knowledge without a per-finding owner disposition.
    page = 1
    while True:
        payload = governance.request_json(repository, token, "GET", f"pulls/{pr}/reviews?per_page=100&page={page}")
        if not isinstance(payload, list):
            raise ValueError("reviews payload must be a list")
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("review must be an object")
            body = str(item.get("body") or "").strip()
            if not body:
                continue
            user = item.get("user") if isinstance(item.get("user"), dict) else {}
            reviewer = str(user.get("login") or "")
            if _normalized_login(reviewer) not in trusted_reviewers:
                continue
            observations.append(
                {
                    "source": "github-review",
                    "provider": "github-review",
                    "event_id": f"review-{item.get('id')}",
                    "source_pr": pr,
                    "reviewed_head_sha": head,
                    "reviewed_base_sha": base,
                    "source_event_head_sha": item.get("commit_id"),
                    "reviewer": reviewer or "github-review",
                    "path": None,
                    "line": None,
                    "message": body,
                    "availability": "available",
                    "classification": None,
                    "invariant": None,
                    "affected_paths": [],
                    "fix_reference": None,
                    "regression_evidence": [],
                    "claimed_family_id": None,
                }
            )
        if len(payload) < 100:
            break
        page += 1
    return observations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")
    print(json.dumps(collect(args.repository, token, args.pr, args.head, args.base), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
