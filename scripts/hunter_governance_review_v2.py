"""Trusted governance sanity review plus exact-head candidate admission helpers.

Hunter Governance Review is a required merge prerequisite. A successful status
therefore requires both a clean merge state and successful exact-head candidate
admission; the separate Draft controller remains defense in depth only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import hunter_github_transport as transport

CONTEXT = "Hunter Governance Review"
PRE_PR_WORKFLOW_NAME = "Hunter / Pre-PR Preflight"
PRE_PR_WORKFLOW_PATH = ".github/workflows/hunter-pre-pr-preflight.yml"
PREFLIGHT_UPGRADE_STATUS_PREFIX = "Hunter Trusted Preflight Upgrade / PR #"
ROOT = Path(__file__).resolve().parents[1] if "__file__" in globals() else Path(".")
REVIEWER_DISPOSITIONS_PATH = ROOT / "docs" / "REVIEWER_FINDING_DISPOSITIONS.json"
PREFLIGHT_OWNED_PATHS = frozenset(
    {
        ".githooks/pre-push",
        ".github/workflows/hunter-pre-pr-preflight.yml",
        "scripts/hunter_pr_preflight.py",
        "scripts/hunter_architecture_index_preflight.py",
        "scripts/hunter_artifact_preflight.py",
        "scripts/hunter_defect_prevention_preflight.py",
        "scripts/hunter_pre_push.py",
    }
)


def check_reviewer_dispositions() -> tuple[bool, str]:
    if not REVIEWER_DISPOSITIONS_PATH.is_file():
        return True, ""
    try:
        data = json.loads(REVIEWER_DISPOSITIONS_PATH.read_text(encoding="utf-8"))
        findings = data.get("findings", [])
        for f in findings:
            if (
                isinstance(f, dict)
                and f.get("validation_state") == "validated"
                and f.get("resolution_state") == "unresolved"
            ):
                fid = f.get("id", "unknown")
                return False, f"Unresolved validated reviewer finding: {fid}"
    except Exception as exc:
        return False, f"Failed to check reviewer dispositions: {exc}"
    return True, ""


def request_json(repository: str, token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    return transport.request_rest_json(
        url=f"https://api.github.com/repos/{repository}/{path}",
        method=method,
        headers={},
        data=data,
        token=token,
        what=f"{method} {path}",
    )


def publish(repository: str, token: str, sha: str, state: str, description: str) -> None:
    run_id = os.environ.get("GITHUB_RUN_ID") or ""
    server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
    target_url = f"{server}/{repository}/actions/runs/{run_id}" if run_id else ""
    request_json(
        repository,
        token,
        "POST",
        f"statuses/{sha}",
        {
            "state": state,
            "context": CONTEXT,
            "description": description[:140],
            "target_url": target_url,
        },
    )
    print(f"{sha[:10]} {CONTEXT}: {state} — {description[:140]}")


def read_mergeability(repository: str, token: str, pr_number: int) -> dict[str, Any]:
    pr: dict[str, Any] = {}
    for attempt in range(3):
        payload = request_json(repository, token, "GET", f"pulls/{pr_number}")
        if not isinstance(payload, dict):
            raise RuntimeError("Pull request payload is unavailable")
        pr = payload
        if pr.get("mergeable") is not None:
            break
        if attempt < 2:
            time.sleep(2)
    return pr


def read_pr_changed_paths(repository: str, token: str, pr_number: int) -> tuple[bool, tuple[str, ...], str | None]:
    collected: list[str] = []
    try:
        for page in range(1, 31):
            payload = request_json(
                repository,
                token,
                "GET",
                f"pulls/{pr_number}/files?per_page=100&page={page}",
            )
            if not isinstance(payload, list):
                return False, (), "pull request file listing payload is not a list"
            collected.extend(
                str(item.get("filename") or "").strip()
                for item in payload
                if isinstance(item, dict) and item.get("filename")
            )
            if len(payload) < 100:
                return True, tuple(collected), None
        return False, (), "pull request file listing exceeds the supported 3000-file proof boundary"
    except transport.GitHubRequestError as exc:
        return False, (), f"GitHub request error: {exc}"
    except Exception as exc:
        return False, (), f"unexpected error: {type(exc).__name__}: {exc}"


CODE_WRITE_POLICY_PATH = ROOT / "docs" / "CODE_WRITE_POLICY.json"
COMMIT_PAGE_CAP = 30


def _is_commit_sha(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def load_ingress_provenance_policy() -> tuple[frozenset[str], str, str | None]:
    """Read the trusted code-write ingress provenance authority.

    The governance controller checks out the default branch, so this reads the
    trusted policy rather than the candidate's own copy: a candidate cannot widen
    its own signer allowlist or move its own attestation floor.
    """
    try:
        policy = json.loads(CODE_WRITE_POLICY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return frozenset(), "", "canonical code-write policy is missing"
    except Exception as exc:
        return frozenset(), "", f"canonical code-write policy is unreadable ({type(exc).__name__}: {exc})"

    provenance = policy.get("ingress_provenance") if isinstance(policy, dict) else None
    if not isinstance(provenance, dict):
        return frozenset(), "", "canonical code-write policy declares no ingress provenance authority"

    raw_signers = provenance.get("authorized_signers")
    if not isinstance(raw_signers, list) or not raw_signers:
        return frozenset(), "", "code-write policy declares no authorized ingress signers"
    signers = {str(signer).strip().lower() for signer in raw_signers if isinstance(signer, str) and str(signer).strip()}
    if len(signers) != len(raw_signers):
        return frozenset(), "", "code-write policy authorized_signers contains a malformed entry"

    floor = provenance.get("attested_from_commit", "")
    if not isinstance(floor, str):
        return frozenset(), "", "code-write policy attested_from_commit is malformed"
    floor = floor.strip().lower()
    if floor and not _is_commit_sha(floor):
        return frozenset(), "", "code-write policy attested_from_commit is not a full commit SHA"
    return frozenset(signers), floor, None


def load_connector_write_ingress_policy() -> tuple[bool, frozenset[str], str | None]:
    """Read the trusted connector code-write ingress grant (Issue #403).

    Like the pre-push signer authority this is read from the trusted default
    branch, never from the candidate head, so a candidate cannot grant itself
    connector ingress. A policy with no grant simply grants nothing; a malformed
    grant blocks admission rather than being ignored.
    """
    try:
        policy = json.loads(CODE_WRITE_POLICY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, frozenset(), "canonical code-write policy is missing"
    except Exception as exc:
        return False, frozenset(), f"canonical code-write policy is unreadable ({type(exc).__name__}: {exc})"

    grant = policy.get("connector_write_ingress") if isinstance(policy, dict) else None
    if grant is None:
        return False, frozenset(), None
    if not isinstance(grant, dict):
        return False, frozenset(), "connector write ingress grant is malformed"
    if grant.get("local_pre_push_equivalent") is not False:
        return False, frozenset(), "connector write ingress must not declare local pre-push equivalence"

    enabled = grant.get("enabled")
    if not isinstance(enabled, bool):
        return False, frozenset(), "connector write ingress grant declares no explicit enabled state"

    raw_writers = grant.get("authorized_writers")
    if not isinstance(raw_writers, list):
        return False, frozenset(), "connector write ingress authorized_writers must be a list"

    logins: set[str] = set()
    for entry in raw_writers:
        if not isinstance(entry, dict):
            return False, frozenset(), "connector write ingress authorized_writers contains a malformed entry"
        login = entry.get("login")
        if not isinstance(login, str):
            return False, frozenset(), "connector write ingress writer login must be a string"
        login = login.strip().lower()
        if login:
            logins.add(login)

    if enabled and not logins:
        return False, frozenset(), "connector write ingress is enabled but binds no writer identity"
    return enabled, frozenset(logins), None


def read_pr_commits(repository: str, token: str, pr_number: int) -> tuple[bool, tuple[dict[str, Any], ...], str | None]:
    collected: list[dict[str, Any]] = []
    try:
        for page in range(1, COMMIT_PAGE_CAP + 1):
            payload = request_json(
                repository,
                token,
                "GET",
                f"pulls/{pr_number}/commits?per_page=100&page={page}",
            )
            if not isinstance(payload, list):
                return False, (), "pull request commit listing payload is not a list"
            collected.extend(item for item in payload if isinstance(item, dict))
            if len(payload) < 100:
                return True, tuple(collected), None
        return False, (), "pull request commit range exceeds the supported proof boundary"
    except transport.GitHubRequestError as exc:
        return False, (), f"GitHub request error: {exc}"
    except Exception as exc:
        return False, (), f"unexpected error: {type(exc).__name__}: {exc}"


def read_commits_beyond_attestation_floor(
    repository: str,
    token: str,
    floor_sha: str,
    head_sha: str,
) -> tuple[bool, frozenset[str], str | None]:
    """SHAs reachable from head_sha but not from the declared attestation floor.

    Three-dot comparison excludes only commits reachable from the merge base of
    the floor and the head. Any excluded commit is therefore an ancestor of the
    floor, so a candidate cannot steer the comparison into exempting a commit
    written after the authority activated.
    """
    collected: set[str] = set()
    encoded_floor = quote(floor_sha, safe="")
    encoded_head = quote(head_sha, safe="")
    try:
        for page in range(1, COMMIT_PAGE_CAP + 1):
            payload = request_json(
                repository,
                token,
                "GET",
                f"compare/{encoded_floor}...{encoded_head}?per_page=100&page={page}",
            )
            if not isinstance(payload, dict):
                return False, frozenset(), "commit range comparison payload is malformed"
            commits = payload.get("commits")
            if not isinstance(commits, list):
                return False, frozenset(), "commit range comparison payload is malformed"
            collected.update(
                str(commit.get("sha") or "").strip().lower()
                for commit in commits
                if isinstance(commit, dict) and commit.get("sha")
            )
            if len(commits) < 100:
                return True, frozenset(collected), None
        return False, frozenset(), "commit range comparison exceeds the supported proof boundary"
    except transport.GitHubRequestError as exc:
        return False, frozenset(), f"GitHub request error: {exc}"
    except Exception as exc:
        return False, frozenset(), f"unexpected error: {type(exc).__name__}: {exc}"


def _ingress_signer(entry: dict[str, Any]) -> str:
    """GitHub account that wrote the commit.

    Bound to the committer only. A verified signature requires the committer
    email to be a verified email of the signing key's owner, so this login is
    cryptographically bound; the author field carries no such binding.
    """
    committer = entry.get("committer")
    if not isinstance(committer, dict):
        return ""
    return str(committer.get("login") or "").strip().lower()


def verify_code_write_ingress_provenance(
    repository: str,
    token: str,
    head_sha: str,
    pr_number: int | None,
) -> tuple[bool, str]:
    """Require positive pre-push ingress proof for the whole code-changing range.

    Commit identity fields are caller-supplied through the Contents API and Git
    Data API, so they prove nothing about how a ref was written. A verified
    signature from an authorized clone-capable writer is the positive proof: it
    is computed over the commit object, so it binds the exact SHA and cannot be
    replayed onto another commit, forged as commit metadata, or satisfied by
    hosted CI success. Every commit in the range is checked, so a clone-authored
    tip cannot conceal an API-written ancestor.
    """
    if pr_number is None:
        return False, "Candidate admission blocked: ingress provenance requires PR-bound commit-range evidence."

    signers, floor_sha, policy_error = load_ingress_provenance_policy()
    if policy_error is not None:
        return False, f"Candidate admission blocked: {policy_error}."

    ok, commits, error = read_pr_commits(repository, token, pr_number)
    if not ok:
        return False, f"Candidate admission blocked: commit-range ingress evidence is unavailable ({error})."
    if not commits:
        return False, "Candidate admission blocked: commit-range ingress evidence is empty."

    range_shas = {str(entry.get("sha") or "").strip().lower() for entry in commits}
    if not all(_is_commit_sha(sha) for sha in range_shas):
        return False, "Candidate admission blocked: commit-range ingress evidence carries a malformed commit SHA."
    if head_sha.strip().lower() not in range_shas:
        return False, "Candidate admission blocked: exact head is absent from the PR commit range evidence."

    attestation_required = range_shas
    if floor_sha and floor_sha in range_shas:
        ok_floor, beyond_floor, floor_error = read_commits_beyond_attestation_floor(
            repository, token, floor_sha, head_sha
        )
        if not ok_floor:
            return False, f"Candidate admission blocked: pre-authority range evidence is unavailable ({floor_error})."
        attestation_required = range_shas & beyond_floor

    connector_enabled, connector_logins, connector_error = load_connector_write_ingress_policy()
    if connector_error is not None:
        return False, f"Candidate admission blocked: {connector_error}."
    if connector_enabled and (connector_logins & signers):
        # A connector identity that is also a clone-capable pre-push signer would
        # let a connector write be counted as local pre-push proof, which is
        # exactly the equivalence Issue #403 requirement 7 forbids.
        return False, ("Candidate admission blocked: connector ingress writer is also a clone-capable pre-push signer.")

    connector_written: set[str] = set()
    for entry in commits:
        sha = str(entry.get("sha") or "").strip().lower()
        if sha not in attestation_required:
            continue
        verification = (entry.get("commit") or {}).get("verification")
        if not isinstance(verification, dict):
            return False, f"Candidate admission blocked: commit {sha[:10]} carries no ingress signature evidence."
        reason = str(verification.get("reason") or "unknown")
        if verification.get("verified") is not True or reason != "valid":
            return False, (
                f"Candidate admission blocked: commit {sha[:10]} has no verified pre-push ingress "
                f"signature (reason={reason})."
            )
        signer = _ingress_signer(entry)
        if signer in signers:
            continue
        if connector_enabled and signer in connector_logins:
            connector_written.add(sha)
            continue
        return False, (
            f"Candidate admission blocked: commit {sha[:10]} was written by unauthorized ingress "
            f"signer {signer or 'unknown'}."
        )

    if connector_written:
        # A connector write is never local pre-push proof. The equivalent trusted
        # admission path is the hosted exact-head canonical preflight, and until
        # it succeeds for this exact head the candidate stays unadmitted.
        proof_state, proof_description = read_trusted_upgrade_status(repository, token, head_sha, pr_number)
        if proof_state != "success":
            return False, (
                f"Candidate admission blocked: {len(connector_written)} connector-written commit(s) require "
                f"trusted hosted exact-head canonical preflight proof ({proof_description})"
            )
        return True, "Connector ingress commits are covered by trusted hosted exact-head canonical preflight proof."

    return True, "Verified pre-push ingress signatures cover the code-changing commit range."


def read_head_preflight_mode(repository: str, token: str, head_sha: str) -> tuple[str, str | None]:
    encoded_sha = quote(head_sha, safe="")
    try:
        payload = request_json(
            repository,
            token,
            "GET",
            f"contents/.hunter-preflight-mode?ref={encoded_sha}",
        )
    except transport.GitHubRequestError as exc:
        if exc.status_code == 404:
            return "normal", None
        return "unavailable", f"GitHub request error ({exc.status_code}): {exc}"
    except Exception as exc:
        return "unavailable", f"unexpected error: {type(exc).__name__}: {exc}"

    if isinstance(payload, dict):
        if payload.get("message") == "Not Found":
            return "normal", None
        content = payload.get("content")
        if isinstance(content, str) and content:
            try:
                raw = base64.b64decode(content).decode("utf-8").strip()
            except Exception as exc:
                return "invalid", f"failed to decode base64 mode content: {exc}"
            if raw == "tests-first-red":
                return "tests-first-red", None
            return "invalid", f"unsupported preflight mode content: {raw!r}"
        return "normal", None
    return "unavailable", "non-dict payload for .hunter-preflight-mode"


def _upgrade_status_context(pr_number: int) -> str:
    return f"{PREFLIGHT_UPGRADE_STATUS_PREFIX}{pr_number}"


def read_trusted_upgrade_status(
    repository: str,
    token: str,
    head_sha: str,
    pr_number: int,
) -> tuple[str, str]:
    encoded_sha = quote(head_sha, safe="")
    payload = request_json(repository, token, "GET", f"commits/{encoded_sha}/statuses?per_page=100")
    if not isinstance(payload, list):
        return "failure", "Candidate admission blocked: trusted upgrade status evidence is malformed."

    context = _upgrade_status_context(pr_number)
    matching = [
        status for status in payload if isinstance(status, dict) and str(status.get("context") or "") == context
    ]
    if not matching:
        return "missing", "Candidate admission blocked: exact-head trusted preflight upgrade status is missing."

    latest = max(matching, key=lambda status: int(status.get("id") or 0))
    state = str(latest.get("state") or "").strip()
    if state == "success":
        return "success", "Exact-head trusted candidate preflight validation passed."
    if state == "pending":
        return "pending", "Waiting for exact-head trusted candidate preflight validation."
    return "failure", f"Candidate admission blocked: trusted candidate preflight validation={state or 'unknown'}."


def candidate_admission(repository: str, token: str, head_sha: str, pr_number: int | None = None) -> tuple[str, str]:
    touches_protected_preflight = False
    if pr_number is not None:
        ok, changed_paths, error = read_pr_changed_paths(repository, token, pr_number)
        if not ok:
            return "failure", f"Candidate admission blocked: changed-file evidence is unavailable ({error})."
        touches_protected_preflight = any(path in PREFLIGHT_OWNED_PATHS for path in changed_paths)

    head_mode, mode_error = read_head_preflight_mode(repository, token, head_sha)
    if head_mode == "unavailable":
        return "failure", f"Candidate admission blocked: preflight mode evidence is unavailable ({mode_error})."
    if head_mode == "invalid":
        return "failure", f"Candidate admission blocked: invalid .hunter-preflight-mode content ({mode_error})."
    if head_mode == "tests-first-red":
        return "failure", "Candidate admission blocked: tests-first-red work must remain Draft-only."

    ok_ingress, ingress_message = verify_code_write_ingress_provenance(repository, token, head_sha, pr_number)
    if not ok_ingress:
        return "failure", ingress_message

    if touches_protected_preflight:
        if pr_number is None:
            return "failure", "Candidate admission blocked: protected preflight changes require PR-bound proof."
        proof_state, proof_description = read_trusted_upgrade_status(repository, token, head_sha, pr_number)
        if proof_state == "missing":
            return "failure", proof_description
        return proof_state, proof_description

    encoded_sha = quote(head_sha, safe="")
    payload = request_json(
        repository,
        token,
        "GET",
        f"actions/runs?head_sha={encoded_sha}&event=push&per_page=100",
    )
    if not isinstance(payload, dict):
        return "failure", "Candidate admission blocked: branch preflight run evidence is malformed."

    workflow_runs = payload.get("workflow_runs")
    if not isinstance(workflow_runs, list):
        return "failure", "Candidate admission blocked: workflow_runs payload is malformed."

    matching = [
        run
        for run in workflow_runs
        if isinstance(run, dict)
        and str(run.get("head_sha") or "") == head_sha
        and str(run.get("name") or "") == PRE_PR_WORKFLOW_NAME
        and str(run.get("path") or "") == PRE_PR_WORKFLOW_PATH
        and str(run.get("event") or "") == "push"
    ]
    if not matching:
        return "failure", "Candidate admission blocked: exact-head branch preflight is missing."

    latest = max(matching, key=lambda run: int(run.get("id") or 0))
    status = str(latest.get("status") or "")
    conclusion = str(latest.get("conclusion") or "")
    if status != "completed":
        return "pending", "Waiting for exact-head branch preflight to complete."
    if conclusion != "success":
        return "failure", f"Candidate admission blocked: exact-head branch preflight={conclusion or 'unknown'}."
    return "success", "Exact-head branch preflight passed before review progression."


def review(repository: str, token: str, pr_number: int) -> int:
    pr = read_mergeability(repository, token, pr_number)
    if pr.get("state") != "open":
        print(f"PR #{pr_number} is not open; no governance status published.")
        return 0

    base_ref = str((pr.get("base") or {}).get("ref") or "").strip()
    if base_ref != "main":
        print(f"PR #{pr_number} targets {base_ref or 'an unavailable base'}; no governance status published.")
        return 0

    head_sha = str((pr.get("head") or {}).get("sha") or "").strip()
    if not head_sha:
        raise RuntimeError(f"PR #{pr_number} head SHA is unavailable")

    disp_ok, disp_msg = check_reviewer_dispositions()
    if not disp_ok:
        publish(repository, token, head_sha, "failure", f"Blocking governance finding: {disp_msg}")
        return 0

    if pr.get("mergeable") is False:
        publish(
            repository, token, head_sha, "failure", "Blocking governance finding: pull request has merge conflicts."
        )
        return 0
    if pr.get("mergeable") is None:
        publish(repository, token, head_sha, "pending", "Waiting for GitHub to resolve current mergeability.")
        return 0

    admission_state, admission_description = candidate_admission(repository, token, head_sha, pr_number)
    if admission_state != "success":
        status_state = "pending" if admission_state == "pending" else "failure"
        publish(repository, token, head_sha, status_state, admission_description)
        return 0

    publish(
        repository,
        token,
        head_sha,
        "success",
        "Exact-head candidate admission and current merge-state governance checks passed.",
    )
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Hunter lightweight governance sanity review")
    result.add_argument("--pr", type=int, required=True)
    result.add_argument("--repository", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        return review(args.repository, token, args.pr)
    except transport.GitHubUnavailable as exc:
        print(f"Governance review infrastructure unavailable: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Governance review failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
