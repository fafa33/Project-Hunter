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
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import hunter_connector_write_ingress as ingress
import hunter_github_transport as transport
import hunter_pre_ready_review as pre_ready


@dataclass(frozen=True)
class ConnectorAdmission:
    """Whether a candidate is connector-origin, and whether its evidence holds.

    Three outcomes, deliberately distinct (Issue #409):

    * ``ok and not origin`` -- an ordinary candidate. It never touched the
      connector channel, so the pre-existing verified-signature / pre-push
      evidence applies to it unchanged.
    * ``ok and origin`` -- a candidate proven connector-origin under the trusted
      default-branch grant: connector namespace, exact-head receipt, and every
      receipt claim re-derived from trusted repository and pull-request evidence.
      Such a candidate is admitted on connector evidence instead of a local
      commit signature, which the GitHub connector API cannot produce.
    * ``not ok`` -- the connector evidence was required and failed. `message`
      carries the blocking reason.

    `origin` is never a claim a candidate makes about itself. It is the
    *conclusion* of the trusted re-derivation, so a signature can neither
    establish it nor substitute for any part of it.
    """

    ok: bool
    origin: bool
    message: str
    writer: str = ""


CONTEXT = "Hunter Governance Review"
PRE_PR_WORKFLOW_NAME = "Hunter / Pre-PR Preflight"
PRE_PR_WORKFLOW_PATH = ".github/workflows/hunter-pre-pr-preflight.yml"
PREFLIGHT_UPGRADE_STATUS_PREFIX = "Hunter Trusted Preflight Upgrade / PR #"
TRUSTED_UPGRADE_WORKFLOW_NAME = "Hunter / Trusted Preflight Upgrade"
TRUSTED_UPGRADE_WORKFLOW_PATH = ".github/workflows/hunter-trusted-preflight-upgrade.yml"
TRUSTED_STATUS_CREATOR = "github-actions[bot]"
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


@dataclass(frozen=True, order=True)
class PullRequestFile:
    status: str
    path: str
    previous_path: str = ""
    blob_sha: str = ""

    def affected_paths(self) -> tuple[str, ...]:
        if self.status == "renamed":
            return (self.previous_path, self.path)
        return (self.path,)


def read_pr_changed_files(
    repository: str, token: str, pr_number: int
) -> tuple[bool, tuple[PullRequestFile, ...], str | None]:
    """Trusted operation and exact-head content for every changed file.

    The blob SHA is GitHub's own hash of the file content at the exact head, so it
    is what lets the trusted controller bind an authorization to the *content* it
    authorized rather than only to the path set (Issue #409 review).

    GitHub reports a base blob SHA for a removed file, so removal is represented
    as an empty destination blob. Renames preserve both names. Malformed evidence
    remains present and therefore fails the connector re-derivation closed.
    """
    collected: list[PullRequestFile] = []
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
            for item in payload:
                if not isinstance(item, dict) or not item.get("filename"):
                    continue
                filename = str(item.get("filename") or "").strip()
                status = str(item.get("status") or "").strip().lower()
                previous = str(item.get("previous_filename") or "").strip()
                digest = str(item.get("sha") or "").strip().lower()
                collected.append(
                    PullRequestFile(
                        status=status,
                        path=filename,
                        previous_path=previous,
                        blob_sha="" if status == "removed" else (digest if _is_commit_sha(digest) else ""),
                    )
                )
            if len(payload) < 100:
                return True, tuple(collected), None
        return False, (), "pull request file listing exceeds the supported 3000-file proof boundary"
    except transport.GitHubRequestError as exc:
        return False, (), f"GitHub request error: {exc}"
    except Exception as exc:
        return False, (), f"unexpected error: {type(exc).__name__}: {exc}"


def read_pr_changed_paths(repository: str, token: str, pr_number: int) -> tuple[bool, tuple[str, ...], str | None]:
    ok, files, error = read_pr_changed_files(repository, token, pr_number)
    return ok, tuple(sorted({path for item in files for path in item.affected_paths() if path})), error


CODE_WRITE_POLICY_RELATIVE_PATH = "docs/CODE_WRITE_POLICY.json"
CODE_WRITE_POLICY_PATH = ROOT / CODE_WRITE_POLICY_RELATIVE_PATH
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

    An *active* grant means the connector writes through an account that also
    appears in the clone-capable signer allowlist, so committer login can no
    longer separate the two channels. The grant must therefore declare that the
    trusted hosted exact-head proof is required for every candidate; a grant that
    is active without that declaration is refused rather than silently reducing
    admission to signature-only.
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

    if not enabled:
        return False, frozenset(), None
    if not logins:
        return False, frozenset(), "connector write ingress is enabled but binds no writer identity"

    admission = grant.get("hosted_admission")
    if not isinstance(admission, dict):
        return False, frozenset(), "connector write ingress declares no hosted admission path"
    if admission.get("require_for_all_candidates") is not True:
        return (
            False,
            frozenset(),
            ("active connector write ingress must require trusted hosted exact-head proof for all candidates"),
        )
    return True, frozenset(logins), None


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
) -> tuple[str, str]:
    """Require positive ingress proof for the whole code-changing range.

    Two channels write this repository, and each has its own positive proof. The
    channel is decided first, by trusted evidence, and only then is the matching
    proof required.

    **Clone-capable writers** prove ingress with a verified commit signature.
    Commit identity fields are caller-supplied through the Contents API and Git
    Data API, so they prove nothing about how a ref was written; a signature is
    computed over the commit object, so it binds the exact SHA and cannot be
    replayed onto another commit, forged as commit metadata, or satisfied by
    hosted CI success. Every commit in the range is checked, so a clone-authored
    tip cannot conceal an API-written ancestor. This path is unchanged.

    **Connector writers** cannot produce that signature at all -- the GitHub
    connector API creates commits server-side, so there is no local key and no
    pre-push boundary to sign at. Requiring one made the authorized connector
    path unusable (Issue #409: PR #408 was blocked as `unsigned` despite being a
    correctly authorized `connector/issue-402-*` candidate). So a candidate proven
    connector-origin is admitted on the connector channel's own evidence instead:
    the exact-head authorization receipt with every claim re-derived from trusted
    repository and pull-request evidence, one authorized writer committing the
    whole range, and the trusted hosted exact-head canonical preflight proof.

    Connector evidence is **never** credited as local pre-push proof. It is a
    different proof of a different channel, and the substitution is one-way:
    connector origin is established only by the trusted re-derivation, so a
    clone-capable writer cannot reach the connector regime by asserting it, and
    an ordinary candidate still needs its signatures.

    Returns a tri-state ``(state, message)`` where state is ``success``,
    ``pending`` or ``failure``. Issue #412: a hosted proof that is still running
    is not a defect in the candidate, and reporting it as ``failure`` published a
    red signal for a candidate that was merely early. Only ``pending`` is
    transient; an invalid, absent, untrusted or wrongly-bound proof stays
    ``failure``.
    """
    if pr_number is None:
        return "failure", "Candidate admission blocked: ingress provenance requires PR-bound commit-range evidence."

    signers, floor_sha, policy_error = load_ingress_provenance_policy()
    if policy_error is not None:
        return "failure", f"Candidate admission blocked: {policy_error}."

    ok, commits, error = read_pr_commits(repository, token, pr_number)
    if not ok:
        return "failure", f"Candidate admission blocked: commit-range ingress evidence is unavailable ({error})."
    if not commits:
        return "failure", "Candidate admission blocked: commit-range ingress evidence is empty."

    range_shas = {str(entry.get("sha") or "").strip().lower() for entry in commits}
    if not all(_is_commit_sha(sha) for sha in range_shas):
        return "failure", "Candidate admission blocked: commit-range ingress evidence carries a malformed commit SHA."
    if head_sha.strip().lower() not in range_shas:
        return "failure", "Candidate admission blocked: exact head is absent from the PR commit range evidence."

    attestation_required = range_shas
    if floor_sha and floor_sha in range_shas:
        ok_floor, beyond_floor, floor_error = read_commits_beyond_attestation_floor(
            repository, token, floor_sha, head_sha
        )
        if not ok_floor:
            return (
                "failure",
                f"Candidate admission blocked: pre-authority range evidence is unavailable ({floor_error}).",
            )
        attestation_required = range_shas & beyond_floor

    connector_enabled, connector_logins, connector_error = load_connector_write_ingress_policy()
    if connector_error is not None:
        return "failure", f"Candidate admission blocked: {connector_error}."

    # Which channel wrote this candidate is decided FIRST, from trusted evidence,
    # because the two channels have different -- and mutually impossible --
    # proofs. Deriving the committer of every commit from the trusted listing
    # rather than from signature results is what makes that possible: a connector
    # write carries no signature, so identity taken from signature results would
    # make connector origin unprovable by construction (Issue #409).
    ok_paths, changed_files, paths_error = read_pr_changed_files(repository, token, pr_number)
    if not ok_paths:
        return "failure", f"Candidate admission blocked: changed-file evidence is unavailable ({paths_error})."
    range_commits = tuple(entry for entry in commits if str(entry.get("sha") or "").strip().lower() in range_shas)
    connector = verify_connector_ingress_authorization(
        repository, token, head_sha, pr_number, changed_files, range_commits
    )
    if not connector.ok:
        return "failure", connector.message

    if connector.origin:
        # Connector-origin: the trusted re-derivation above already proved the
        # writer, capability, namespace, Issue binding, exact base tip, exact
        # changed-path scope, root-of-trust exclusion, receipt validity, grant
        # fingerprint, and absence of same-candidate self-escalation. What
        # remains is the proof no writer on any channel can mint.
        if not connector_enabled:
            return "failure", (
                "Candidate admission blocked: this candidate is connector-origin but the connector write "
                "ingress is not active in trusted default-branch state."
            )
        if connector.writer not in connector_logins:
            return "failure", (
                f"Candidate admission blocked: connector writer {connector.writer!r} is not bound by the "
                "trusted connector grant."
            )
        proof_state, proof_description = read_trusted_upgrade_status(repository, token, head_sha, pr_number)
        if proof_state != "success":
            # A proof that is still running is not an invalid proof (Issue #412).
            return ("pending" if proof_state == "pending" else "failure"), (
                "Candidate admission is waiting: " if proof_state == "pending" else "Candidate admission blocked: "
            ) + (
                "a connector-origin range is admitted on connector evidence rather than a local commit "
                "signature, so it requires trusted hosted exact-head canonical preflight proof "
                f"({proof_description})"
            )
        return "success", (
            f"{connector.message} Connector-origin range admitted on connector evidence plus trusted hosted "
            "exact-head canonical preflight proof; this is not local pre-push proof."
        )

    # Ordinary candidate: the pre-existing verified-signature requirement, unchanged.
    #
    # The connector authenticates as an account that is also a clone-capable
    # signer, so committer login cannot say which channel wrote a commit and
    # demanding disjoint identities would only make the grant unbindable. The
    # channels are separated by evidence instead: while the grant is active a
    # verified signature is no longer sufficient on its own for ANY candidate,
    # and the trusted hosted exact-head proof is additionally required. That
    # proof is minted by the trusted default-branch controller against the exact
    # candidate SHA, so no writer on either channel can produce it.
    authorized_ingress = (signers | connector_logins) if connector_enabled else signers

    for entry in commits:
        sha = str(entry.get("sha") or "").strip().lower()
        if sha not in attestation_required:
            continue
        verification = (entry.get("commit") or {}).get("verification")
        if not isinstance(verification, dict):
            return "failure", f"Candidate admission blocked: commit {sha[:10]} carries no ingress signature evidence."
        reason = str(verification.get("reason") or "unknown")
        if verification.get("verified") is not True or reason != "valid":
            return "failure", (
                f"Candidate admission blocked: commit {sha[:10]} has no verified pre-push ingress "
                f"signature (reason={reason})."
            )
        signer = _ingress_signer(entry)
        if signer not in authorized_ingress:
            return "failure", (
                f"Candidate admission blocked: commit {sha[:10]} was written by unauthorized ingress "
                f"signer {signer or 'unknown'}."
            )

    if connector_enabled and attestation_required:
        proof_state, proof_description = read_trusted_upgrade_status(repository, token, head_sha, pr_number)
        if proof_state != "success":
            # A proof that is still running is not an invalid proof (Issue #412).
            return ("pending" if proof_state == "pending" else "failure"), (
                "Candidate admission is waiting: " if proof_state == "pending" else "Candidate admission blocked: "
            ) + (
                "the connector write ingress is active, so a verified signature alone is not pre-push proof; "
                "this range requires trusted hosted exact-head canonical preflight proof "
                f"({proof_description})"
            )
        return "success", (
            "Verified ingress signatures plus trusted hosted exact-head canonical preflight proof cover "
            "the code-changing commit range."
        )

    return "success", "Verified pre-push ingress signatures cover the code-changing commit range."


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


def read_head_authorization_receipt(repository: str, token: str, head_sha: str) -> tuple[str, Any, str | None]:
    """Read the connector authorization receipt committed at the exact head.

    Returns ``("absent"|"present"|"unavailable", payload, error)``. The receipt is
    candidate-authored content, so it is only ever a declaration to be checked
    against trusted evidence -- never evidence in its own right.
    """
    encoded_sha = quote(head_sha, safe="")
    encoded_path = quote(ingress.AUTHORIZATION_RECEIPT_PATH, safe="/")
    try:
        payload = request_json(repository, token, "GET", f"contents/{encoded_path}?ref={encoded_sha}")
    except transport.GitHubRequestError as exc:
        if exc.status_code == 404:
            return "absent", None, None
        return "unavailable", None, f"GitHub request error ({exc.status_code}): {exc}"
    except Exception as exc:
        return "unavailable", None, f"unexpected error: {type(exc).__name__}: {exc}"

    if not isinstance(payload, dict):
        return "unavailable", None, "non-dict payload for the authorization receipt"
    if payload.get("message") == "Not Found":
        return "absent", None, None
    content = payload.get("content")
    if not isinstance(content, str) or not content:
        return "unavailable", None, "authorization receipt carries no content"
    try:
        raw = base64.b64decode(content).decode("utf-8")
        return "present", json.loads(raw), None
    except Exception as exc:
        return "unavailable", None, f"authorization receipt is undecodable: {type(exc).__name__}: {exc}"


def read_head_code_write_policy(repository: str, token: str, head_sha: str) -> tuple[str, Any, str | None]:
    """Read the candidate head's own copy of the canonical code-write policy.

    Returns ``("absent"|"present"|"unavailable", document, error)``. This is never
    read as authority -- the grant that authorizes a candidate is always the
    default-branch one the controller has checked out. It is read only so the
    controller can see whether a candidate proposes a *wider* grant than the one
    it is being evaluated under, which is the same-pull-request self-escalation
    Issue #405 forbids.
    """
    encoded_sha = quote(head_sha, safe="")
    encoded_path = quote(CODE_WRITE_POLICY_RELATIVE_PATH, safe="/")
    try:
        payload = request_json(repository, token, "GET", f"contents/{encoded_path}?ref={encoded_sha}")
    except transport.GitHubRequestError as exc:
        if exc.status_code == 404:
            return "absent", None, None
        return "unavailable", None, f"GitHub request error ({exc.status_code}): {exc}"
    except Exception as exc:
        return "unavailable", None, f"unexpected error: {type(exc).__name__}: {exc}"

    if not isinstance(payload, dict):
        return "unavailable", None, "non-dict payload for the canonical code-write policy"
    if payload.get("message") == "Not Found":
        return "absent", None, None
    content = payload.get("content")
    if not isinstance(content, str) or not content:
        return "unavailable", None, "canonical code-write policy carries no content"
    try:
        return "present", json.loads(base64.b64decode(content).decode("utf-8")), None
    except Exception as exc:
        return "unavailable", None, f"canonical code-write policy is undecodable: {type(exc).__name__}: {exc}"


def verify_no_same_candidate_self_escalation(
    repository: str,
    token: str,
    head_sha: str,
    trusted: ingress.ConnectorIngressPolicy,
) -> tuple[bool, str]:
    """Refuse a connector candidate that widens the grant it is authorized under.

    A governed pull request is exactly where a wider grant *should* be proposed,
    so this does not forbid the proposal. It forbids the proposal being in force
    for its own candidate: the head's policy is parsed with the same rules as the
    trusted one, and any additional authority it declares blocks admission of a
    candidate that is itself writing through the ingress. The widened grant takes
    effect only once it is independently reviewed, owner-merged, and therefore
    part of the trusted default branch that evaluates the *next* candidate.
    """
    state, document, error = read_head_code_write_policy(repository, token, head_sha)
    if state == "unavailable":
        return False, f"Candidate admission blocked: head code-write policy evidence is unavailable ({error})."
    if state == "absent":
        return False, "Candidate admission blocked: the candidate head carries no canonical code-write policy."

    candidate, parse_error = ingress.parse_policy(document)
    if candidate is None:
        return (
            False,
            f"Candidate admission blocked: the candidate head's code-write policy is unusable ({parse_error}).",
        )

    widening = ingress.grant_widening(trusted, candidate)
    if widening:
        return False, (
            "Candidate admission blocked: a connector candidate may not widen the grant it is authorized "
            "under and rely on it in the same pull request; this head " + "; ".join(widening) + "."
        )
    return True, ""


def read_pr_refs(repository: str, token: str, pr_number: int) -> tuple[bool, str, str, str | None]:
    """Read trusted head/base branch names; PR authorship is not writer proof."""
    try:
        payload = request_json(repository, token, "GET", f"pulls/{pr_number}")
    except Exception as exc:
        return False, "", "", f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return False, "", "", "pull request payload is malformed"
    head_ref = str((payload.get("head") or {}).get("ref") or "").strip()
    base_ref = str((payload.get("base") or {}).get("ref") or "").strip()
    if not head_ref or not base_ref:
        return False, "", "", "pull request payload carries no head/base ref"
    return True, head_ref, base_ref, None


def read_exact_head_push_actor(
    repository: str, token: str, head_sha: str, head_ref: str
) -> tuple[bool, str, str | None]:
    """Authenticate the actor whose push produced the exact candidate head."""

    encoded_sha = quote(head_sha, safe="")
    try:
        payload = request_json(
            repository,
            token,
            "GET",
            f"actions/runs?head_sha={encoded_sha}&event=push&per_page=100",
        )
    except Exception as exc:
        return False, "", f"{type(exc).__name__}: {exc}"
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        return False, "", "exact-head push workflow-run evidence is malformed"
    matching = [
        run
        for run in runs
        if isinstance(run, dict)
        and str(run.get("head_sha") or "").strip().lower() == head_sha
        and str(run.get("head_branch") or "").strip() == head_ref
        and str(run.get("name") or "") == PRE_PR_WORKFLOW_NAME
        and str(run.get("path") or "") == PRE_PR_WORKFLOW_PATH
        and str(run.get("event") or "") == "push"
    ]
    if not matching:
        return False, "", "trusted workflow carries no run for the exact-head push"
    latest = max(matching, key=lambda run: int(run.get("id") or 0))
    actor = str((latest.get("actor") or {}).get("login") or "").strip().lower()
    if not actor:
        return False, "", "exact-head push workflow run carries no authenticated actor"
    return True, actor, None


def verify_range_push_provenance(
    repository: str,
    token: str,
    head_sha: str,
    head_ref: str,
    writer: str,
    range_commits: tuple[dict[str, Any], ...],
) -> tuple[bool, str]:
    """Authenticate who published the whole candidate range, not each commit alone.

    Issue #412. Requiring every commit to have been a pushed head in its own
    right made a perfectly valid multi-commit candidate permanently inadmissible:
    a single ``git push`` of two locally validated commits gives the intermediate
    commit no push-event workflow run of its own, so it could never acquire the
    evidence, and the only recovery was to rewind the branch and force-push the
    commits one at a time purely to manufacture evidence. That is an evidence
    model defect, not a property worth preserving.

    The secure range-level replacement rests on what a push actually proves.
    Every commit GitHub lists for this pull request is, by construction, an
    ancestor of the exact head. So an authenticated push of that head *published*
    the entire range: the actor of the head push is accountable for all of it,
    exactly as they would be for a single squashed commit. That is the positive
    proof, and it is strictly per-candidate -- the run must name this exact SHA on
    this exact PR branch, so a push of the same head on another branch, or of
    another head, proves nothing here.

    The negative half is what keeps this from being a relaxation: an ancestor that
    *does* carry its own authenticated push run on this branch must have been
    pushed by the same bound writer. A foreign account that pushed a commit into
    this branch is therefore still refused, even though the bound writer later
    pushed a head above it. The only case whose verdict changes is the one the
    Issue names: an ancestor with no authenticated run at all, which is precisely
    the commit that was stranded by publishing two commits in one operation.
    """

    ok_actor, head_actor, actor_error = read_exact_head_push_actor(repository, token, head_sha, head_ref)
    if not ok_actor:
        return False, (
            f"Candidate admission blocked: authenticated push actor evidence for commit {head_sha[:10]} "
            f"is unavailable ({actor_error})."
        )
    if head_actor != writer:
        return False, (
            f"Candidate admission blocked: connector authorization names writer {writer!r}, but commit "
            f"{head_sha[:10]} was pushed by authenticated actor {head_actor!r}."
        )

    for entry in range_commits:
        sha = str(entry.get("sha") or "").strip().lower()
        if not _is_commit_sha(sha):
            return False, "Candidate admission blocked: connector range carries a malformed commit SHA."
        if sha == head_sha:
            continue
        ok_ancestor, ancestor_actor, _error = read_exact_head_push_actor(repository, token, sha, head_ref)
        if ok_ancestor and ancestor_actor != writer:
            return False, (
                f"Candidate admission blocked: connector authorization names writer {writer!r}, but commit "
                f"{sha[:10]} was pushed by authenticated actor {ancestor_actor!r}."
            )

    return True, (
        f"The exact candidate head {head_sha[:10]} was pushed to {head_ref} by authenticated actor {writer!r}, "
        f"which publishes all {len(range_commits)} commit(s) in the range."
    )


def read_merge_base(repository: str, token: str, base_ref: str, head_sha: str) -> tuple[bool, str, str | None]:
    """The trusted fork point of the candidate from the base branch."""
    encoded_base = quote(base_ref, safe="")
    encoded_head = quote(head_sha, safe="")
    try:
        payload = request_json(repository, token, "GET", f"compare/{encoded_base}...{encoded_head}")
    except Exception as exc:
        return False, "", f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return False, "", "commit comparison payload is malformed"
    merge_base = str((payload.get("merge_base_commit") or {}).get("sha") or "").strip().lower()
    if not _is_commit_sha(merge_base):
        return False, "", "commit comparison carries no usable merge base"
    return True, merge_base, None


def verify_connector_ingress_authorization(
    repository: str,
    token: str,
    head_sha: str,
    pr_number: int,
    changed_files: tuple[PullRequestFile, ...],
    range_commits: tuple[dict[str, Any], ...],
) -> ConnectorAdmission:
    """Re-derive every connector ingress constraint from trusted evidence.

    A signed commit from an allowlisted login proves only that an authorized
    account wrote it -- not that the write ever crossed the governed authorizer.
    So the receipt committed at the exact head is parsed for its claims, and then
    every claim is re-checked against trusted repository and pull-request
    evidence: the branch, the base, the fork point, the changed files, and the
    committer of every commit in the range. Nothing is accepted because the
    receipt says so; the receipt only has to agree with what the trusted evidence
    already shows, and the granted scope is re-evaluated against the trusted file
    list rather than the declared one.

    Since Issue #409 this is also what *establishes* connector origin, and it runs
    before any local-signature requirement. Every commit in the connector range
    must have trusted push-workflow actor evidence for this PR branch, so unsigned
    committer metadata is never the authentication for an ancestor either.
    """

    def blocked(reason: str) -> ConnectorAdmission:
        return ConnectorAdmission(ok=False, origin=False, message=reason)

    policy, policy_error = ingress.load_policy()
    if policy is None:
        return blocked(f"Candidate admission blocked: {policy_error}.")

    ok_refs, head_ref, base_ref, refs_error = read_pr_refs(repository, token, pr_number)
    if not ok_refs:
        return blocked(f"Candidate admission blocked: pull-request ref evidence is unavailable ({refs_error}).")

    in_namespace = policy.in_connector_namespace(head_ref)
    state, payload, receipt_error = read_head_authorization_receipt(repository, token, head_sha)
    if state == "unavailable":
        return blocked(f"Candidate admission blocked: authorization receipt evidence is unavailable ({receipt_error}).")

    # A receipt on an ordinary branch is *this candidate's* claim only if this
    # candidate wrote it. A merged connector contribution leaves its receipt on
    # the default branch, so from PR #411 onward every later candidate inherits
    # that file at its head -- and reading mere presence as a claim refused every
    # ordinary branch for "carrying a receipt outside the connector namespace",
    # over an already-merged Issue #407 receipt that says nothing about it.
    # The relaxation is deliberately confined to the ordinary path: a
    # connector-namespace candidate is unchanged, because every claim in its
    # receipt is already re-derived against this exact range, branch, fork point
    # and grant, so an inherited receipt cannot satisfy it anyway. An ordinary
    # branch that actually writes a receipt is still refused.
    receipt_written_here = any(item.path == ingress.AUTHORIZATION_RECEIPT_PATH for item in changed_files)
    if state == "present" and not in_namespace and not receipt_written_here:
        state = "absent"

    if state == "absent":
        if in_namespace:
            return blocked(
                f"Candidate admission blocked: branch {head_ref} is in the connector namespace "
                f"{policy.branch_namespace} but carries no exact-head authorization receipt."
            )
        # Not a connector candidate at all: the ordinary signed/pre-push regime
        # applies to it unchanged.
        return ConnectorAdmission(ok=True, origin=False, message="")
    if not in_namespace:
        return blocked(
            f"Candidate admission blocked: branch {head_ref} carries a connector authorization receipt "
            f"but is outside the connector namespace {policy.branch_namespace}."
        )
    if not policy.enabled:
        return blocked("Candidate admission blocked: connector write ingress is not enabled.")

    authorization, parse_error = ingress.ConnectorWriteAuthorization.from_document(payload)
    if authorization is None:
        return blocked(f"Candidate admission blocked: {parse_error}.")

    if authorization.target_ref != head_ref:
        return blocked(
            f"Candidate admission blocked: authorization is bound to branch {authorization.target_ref!r}, "
            f"not this candidate's {head_ref!r}."
        )
    if authorization.base_ref != base_ref or base_ref != policy.base_ref:
        return blocked(
            f"Candidate admission blocked: authorization base {authorization.base_ref!r} does not match the "
            f"pull-request base {base_ref!r} authorized as {policy.base_ref!r}."
        )

    branch_issue = policy.issue_for_branch(head_ref)
    if branch_issue is None:
        return blocked(
            f"Candidate admission blocked: branch {head_ref} binds no governing Issue under "
            f"{policy.branch_pattern_template}."
        )
    if authorization.issue != branch_issue:
        return blocked(
            f"Candidate admission blocked: authorization claims Issue #{authorization.issue} but the branch "
            f"binds Issue #{branch_issue}."
        )

    writer = authorization.writer.strip().lower()
    granted = policy.capabilities_for(writer)
    if not granted:
        return blocked(f"Candidate admission blocked: authorization writer {writer or 'unknown'!r} is not granted.")
    if authorization.capability not in granted or policy.capability_scope(authorization.capability) is None:
        return blocked(
            f"Candidate admission blocked: authorization capability {authorization.capability!r} is not "
            f"granted to {writer!r}, which holds {', '.join(sorted(granted))}."
        )

    # PR authorship is static and unsigned committer metadata is caller-chosen, so
    # the writer is authenticated at a push boundary on this exact PR branch --
    # at the range level rather than per commit (Issue #412).
    ok_range, range_message = verify_range_push_provenance(repository, token, head_sha, head_ref, writer, range_commits)
    if not ok_range:
        return blocked(range_message)

    ok_no_escalation, escalation_message = verify_no_same_candidate_self_escalation(repository, token, head_sha, policy)
    if not ok_no_escalation:
        return blocked(escalation_message)

    # The grant version is pinned, not merely named: a receipt minted under any
    # other version of the grant -- an older default-branch one, or one the
    # candidate wrote at its own head -- is stale and admits nothing.
    if authorization.grant_fingerprint != policy.fingerprint:
        return blocked(
            "Candidate admission blocked: authorization was minted under grant "
            f"{authorization.grant_fingerprint[:12] or '(none)'}, but the trusted default-branch grant is "
            f"{policy.fingerprint[:12]}; re-authorize against the current grant."
        )

    # Every commit in the range must be committed by the one authorized writer.
    # Derived from the trusted commit listing rather than from signature results,
    # because a connector write carries no signature: this is what stops another
    # account -- including a clone-capable signer -- from riding along inside a
    # connector-origin range.
    # A commit with no committer login is not "no foreign committer": it is an
    # unidentifiable one, so it names itself unknown and still blocks.
    range_committers = frozenset(_ingress_signer(entry) for entry in range_commits)
    foreign = sorted((committer or "unknown") for committer in range_committers if committer != writer)
    if foreign:
        return blocked(
            "Candidate admission blocked: authorization is bound to writer "
            f"{writer!r} but the range carries commits from {', '.join(foreign)}."
        )

    ok_base, merge_base, base_error = read_merge_base(repository, token, base_ref, head_sha)
    if not ok_base:
        return blocked(f"Candidate admission blocked: base provenance evidence is unavailable ({base_error}).")
    if authorization.base_sha.strip().lower() != merge_base:
        return blocked(
            f"Candidate admission blocked: authorization base {authorization.base_sha[:10]} is not this "
            f"candidate's fork point {merge_base[:10]} from {base_ref}."
        )

    # The governance scopes a candidate may use are re-derived from the trusted
    # manifest for the Issue the *branch* binds, never taken from the receipt. A
    # receipt claiming scopes the owner did not authorize for that Issue therefore
    # disagrees with trusted evidence and fails.
    scope = policy.capability_scope(authorization.capability)
    expected_scopes: tuple[str, ...] = ()
    if scope is not None and scope.requires_issue_authorization:
        authorized_scopes = policy.scopes_authorized_for_issue(branch_issue)
        if authorized_scopes is None:
            return blocked(
                f"Candidate admission blocked: Issue #{branch_issue} carries no owner-authored "
                f"{authorization.capability} authorization on the trusted default branch."
            )
        expected_scopes = tuple(sorted(authorized_scopes))
    if tuple(sorted(authorization.governance_scopes)) != expected_scopes:
        return blocked(
            "Candidate admission blocked: authorization claims governance scope(s) "
            f"{', '.join(sorted(authorization.governance_scopes)) or '(none)'}, but Issue #{branch_issue} is "
            f"authorized for {', '.join(expected_scopes) or '(none)'}."
        )

    # Both governance-evidence artifacts are excluded from the authorized content
    # set for the same reason: their meaning is re-derived from trusted evidence
    # rather than granted by the receipt, and a receipt cannot bind an artifact
    # that is minted after it. Excluding the pre-ready review is not optional --
    # it is outside the connector's allowed paths, so binding it would make every
    # connector candidate carrying a review permanently inadmissible while the
    # review gate refuses every connector candidate without one. Both keep the
    # same transition constraint, so neither can be a rename destination for a
    # protected source.
    EVIDENCE_PATHS = {ingress.AUTHORIZATION_RECEIPT_PATH, pre_ready.REVIEW_RELATIVE_PATH}
    evidence_transitions = tuple(item for item in changed_files if item.path in EVIDENCE_PATHS)
    if any(item.status not in {"added", "modified"} or item.previous_path for item in evidence_transitions):
        return blocked(
            "Candidate admission blocked: a governance evidence path may not be deleted, renamed, or used "
            "as a rename destination."
        )
    trusted_files = tuple(item for item in changed_files if item.path not in EVIDENCE_PATHS)
    trusted_changes = ingress.normalize_changes(
        tuple(
            ingress.ConnectorFileChange(item.status, item.path, item.previous_path, item.blob_sha)
            for item in trusted_files
        )
    )
    if trusted_changes is None:
        return blocked(
            "Candidate admission blocked: changed-file operation/content evidence is malformed or ambiguous."
        )
    trusted_paths = tuple(sorted({path for change in trusted_changes for path in change.affected_paths()}))
    if set(trusted_paths) != {p.strip() for p in authorization.paths}:
        return blocked(
            "Candidate admission blocked: the changed files do not match the authorized path set "
            f"({len(trusted_paths)} changed, {len(authorization.paths)} authorized)."
        )

    if trusted_changes != ingress.normalize_changes(authorization.changes):
        return blocked(
            "Candidate admission blocked: the exact file operations or resulting content do not match this "
            "authorization; re-authorize the exact head."
        )
    scope_error = ingress.check_scope(trusted_paths, policy, capability=authorization.capability, issue=branch_issue)
    if scope_error:
        return blocked(f"Candidate admission blocked: connector candidate {scope_error}.")

    return ConnectorAdmission(
        ok=True,
        origin=True,
        writer=writer,
        message=(
            f"Connector authorization {authorization.authorization_id[:12]} ({authorization.capability}) "
            f"re-derived from trusted evidence for Issue #{branch_issue} under grant {policy.fingerprint[:12]}."
        ),
    )


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
    if state == "pending":
        return "pending", "Waiting for exact-head trusted candidate preflight validation."
    if state != "success":
        return "failure", f"Candidate admission blocked: trusted candidate preflight validation={state or 'unknown'}."

    creator = latest.get("creator")
    creator_login = str((creator or {}).get("login") or "").strip().lower()
    creator_type = str((creator or {}).get("type") or "").strip()
    if creator_login != TRUSTED_STATUS_CREATOR or creator_type != "Bot":
        return "failure", "Candidate admission blocked: trusted preflight status has an untrusted publisher."

    target_url = str(latest.get("target_url") or "").strip()
    parsed = urlparse(target_url)
    expected_prefix = f"/{repository}/actions/runs/"
    if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith(expected_prefix):
        return "failure", "Candidate admission blocked: trusted preflight status has no canonical workflow run."
    run_text = parsed.path.removeprefix(expected_prefix)
    if parsed.params or parsed.query or parsed.fragment or not run_text.isdigit() or "/" in run_text:
        return "failure", "Candidate admission blocked: trusted preflight status workflow target is malformed."
    run_id = int(run_text)
    try:
        run = request_json(repository, token, "GET", f"actions/runs/{run_id}")
    except Exception as exc:
        return (
            "failure",
            f"Candidate admission blocked: trusted workflow run is unavailable ({type(exc).__name__}: {exc}).",
        )
    if not isinstance(run, dict):
        return "failure", "Candidate admission blocked: trusted workflow run evidence is malformed."
    if (
        int(run.get("id") or 0) != run_id
        or str(run.get("name") or "") != TRUSTED_UPGRADE_WORKFLOW_NAME
        or str(run.get("path") or "") != TRUSTED_UPGRADE_WORKFLOW_PATH
        or str(run.get("event") or "") != "pull_request_target"
        or str(run.get("head_sha") or "").strip().lower() != head_sha
    ):
        return "failure", "Candidate admission blocked: status does not identify the trusted exact-head workflow."
    run_status = str(run.get("status") or "")
    run_conclusion = str(run.get("conclusion") or "")
    if run_status != "completed":
        return "pending", "Waiting for exact-head trusted candidate preflight workflow to complete."
    if run_conclusion != "success":
        return "failure", f"Candidate admission blocked: trusted workflow conclusion={run_conclusion or 'unknown'}."
    pull_requests = run.get("pull_requests")
    if not isinstance(pull_requests, list) or not any(
        isinstance(pr, dict)
        and int(pr.get("number") or 0) == pr_number
        and str((pr.get("head") or {}).get("sha") or "").strip().lower() == head_sha
        for pr in pull_requests
    ):
        return "failure", "Candidate admission blocked: trusted workflow run is not bound to this exact PR and head."
    return "success", "Exact-head trusted candidate preflight validation passed."


def read_head_pre_ready_review(repository: str, token: str, head_sha: str) -> tuple[str, Any, str | None]:
    """Read the exact-head pre-ready hostile review artifact from trusted evidence."""

    encoded_sha = quote(head_sha, safe="")
    encoded_path = quote(pre_ready.REVIEW_RELATIVE_PATH, safe="/")
    try:
        payload = request_json(repository, token, "GET", f"contents/{encoded_path}?ref={encoded_sha}")
    except transport.GitHubRequestError as exc:
        if exc.status_code == 404:
            return "absent", None, None
        return "unavailable", None, f"GitHub request error: {exc}"
    except Exception as exc:
        return "unavailable", None, f"unexpected error: {type(exc).__name__}: {exc}"
    if not isinstance(payload, dict) or payload.get("encoding") not in (None, "base64"):
        return "unavailable", None, "pre-ready hostile review content evidence is malformed"
    raw = payload.get("content")
    if not isinstance(raw, str):
        return "unavailable", None, "pre-ready hostile review content evidence is malformed"
    try:
        document = json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception as exc:
        return "invalid", None, f"pre-ready hostile review is not readable JSON ({type(exc).__name__}: {exc})"
    return "present", document, None


#: An ordinary feature branch that names the Issue it implements. The connector
#: namespace has its own binding in the grant; this covers the clone-capable
#: naming the repository actually uses (`issue-412-...`, `claude/issue-409-...`).
_BRANCH_ISSUE = re.compile(r"(?:\A|/)issue-([1-9][0-9]{0,9})(?:-|\Z)")


def issue_for_branch(head_ref: str) -> str | None:
    """The Issue a branch name binds, or None when it binds none."""

    match = _BRANCH_ISSUE.search(head_ref.strip())
    return match.group(1) if match is not None else None


def read_issue_acceptance_criteria(repository: str, token: str, issue_number: str) -> tuple[str, tuple[str, ...], str]:
    """The governing Issue's acceptance criteria, from trusted GitHub evidence.

    Returns ``(state, criteria, error)`` where state is ``present`` (the Issue was
    read; criteria may be empty if it defines none) or ``unavailable``. The Issue
    body is owner-authored and read from the API rather than from the candidate,
    so a candidate cannot supply the criteria it will then be measured against.
    """

    try:
        payload = request_json(repository, token, "GET", f"issues/{quote(issue_number, safe='')}")
    except Exception as exc:
        return "unavailable", (), f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return "unavailable", (), "issue payload is malformed"
    if payload.get("pull_request") is not None:
        return "unavailable", (), f"#{issue_number} is a pull request, not the governing Issue"
    body = payload.get("body")
    if body is None:
        return "present", (), ""
    if not isinstance(body, str):
        return "unavailable", (), "issue body evidence is malformed"
    return "present", pre_ready.parse_issue_acceptance_criteria(body), ""


def verify_pre_ready_hostile_review(
    repository: str,
    token: str,
    head_sha: str,
    pr_number: int | None,
) -> tuple[str, str]:
    """Require a complete base->HEAD hostile review bound to this exact content.

    Issue #412. Admission is what lets a candidate stand as Ready, so this is the
    boundary at which "marking Ready must not be the first time a full hostile
    review runs" is actually enforced: an unreviewed, stale, incomplete, or
    still-contested candidate is not admitted, and the candidate-admission
    controller returns it to Draft.

    Nothing here trusts the review's own account of the candidate. The base and
    the exact change set are re-derived from trusted pull-request evidence, and
    the review has to agree with them; the applicable recurring-defect families
    are re-derived from the trusted changed paths, so a review cannot narrow its
    own applicability; and the acceptance criteria are re-derived from the
    owner-authored governing Issue, so a review cannot measure itself against
    criteria it wrote for itself. Unavailable evidence fails closed rather than
    passing for want of proof.
    """

    if pr_number is None:
        return "failure", "Candidate admission blocked: pre-ready hostile review requires PR-bound range evidence."

    families, families_error = pre_ready.load_families()
    if families_error:
        return "failure", f"Candidate admission blocked: {families_error}."

    ok_refs, head_ref, base_ref, refs_error = read_pr_refs(repository, token, pr_number)
    if not ok_refs:
        return "failure", f"Candidate admission blocked: pull-request ref evidence is unavailable ({refs_error})."
    ok_base, merge_base, base_error = read_merge_base(repository, token, base_ref, head_sha)
    if not ok_base:
        return "failure", f"Candidate admission blocked: base provenance evidence is unavailable ({base_error})."

    ok_files, changed_files, files_error = read_pr_changed_files(repository, token, pr_number)
    if not ok_files:
        return "failure", f"Candidate admission blocked: changed-file evidence is unavailable ({files_error})."
    canonical: list[ingress.ConnectorFileChange] = []
    for item in changed_files:
        status = pre_ready.canonical_status(item.status)
        if status is None:
            return (
                "failure",
                f"Candidate admission blocked: changed file {item.path!r} carries unrecognised status "
                f"{item.status!r}, so the pre-ready hostile review cannot be bound to this candidate.",
            )
        # `previous_path` is meaningful only for a rename. GitHub also reports it
        # for a copy, which canonicalises to an addition, and an addition that
        # carried a source would be rejected as malformed -- a false-positive
        # block on a perfectly ordinary change.
        previous_path = item.previous_path if status == "renamed" else ""
        canonical.append(ingress.ConnectorFileChange(status, item.path, previous_path, item.blob_sha))
    changes = ingress.normalize_changes(tuple(canonical))
    if changes is None:
        return (
            "failure",
            "Candidate admission blocked: changed-file operation/content evidence is malformed or ambiguous, "
            "so the pre-ready hostile review cannot be bound to this candidate.",
        )

    changed_paths = tuple(
        sorted({path for change in pre_ready.target_changes(changes) for path in change.affected_paths()})
    )
    applicable = pre_ready.applicable_family_ids(families, changed_paths)

    state, document, read_error = read_head_pre_ready_review(repository, token, head_sha)
    if state == "unavailable":
        return (
            "failure",
            f"Candidate admission blocked: pre-ready hostile review evidence is unavailable ({read_error}).",
        )
    if state == "invalid":
        return "failure", f"Candidate admission blocked: {read_error}."

    if not applicable and document is None:
        # A candidate that triggers no recurring-defect family has no applicable
        # prevention rule to be checked against, so demanding a hostile review of
        # it would be ceremony that blocks valid work -- a dependency pin bump,
        # for instance. A review that *is* present is still verified, so this can
        # never become a way to carry a stale or forged one.
        return "success", "No recurring-defect family applies to this candidate's changed paths."

    # The Issue the review claims must be the Issue the branch binds, when the
    # branch binds one, so a review cannot be measured against a conveniently
    # chosen Issue instead of the one this candidate implements.
    claimed_issue = str(((document or {}).get("claims") or {}).get("issue") or "").strip().lstrip("#")
    branch_issue = issue_for_branch(head_ref)
    if branch_issue is not None and claimed_issue and claimed_issue != branch_issue:
        return "failure", (
            f"Candidate admission blocked: the pre-ready hostile review claims Issue #{claimed_issue}, but branch "
            f"{head_ref} binds Issue #{branch_issue}."
        )

    issue_criteria: tuple[str, ...] | None = None
    if claimed_issue.isdigit():
        state_criteria, issue_criteria, criteria_error = read_issue_acceptance_criteria(
            repository, token, claimed_issue
        )
        if state_criteria != "present":
            return "failure", (
                f"Candidate admission blocked: governing Issue #{claimed_issue} acceptance-criteria evidence is "
                f"unavailable ({criteria_error})."
            )

    verdict = pre_ready.verify_claims(
        document,
        base_sha=merge_base,
        changes=changes,
        families=families,
        issue_criteria=issue_criteria or None,
    )
    if not verdict.ok:
        return "failure", f"Candidate admission blocked: {verdict.reason}."
    claimed_base_ref = str(((document or {}).get("claims") or {}).get("base_ref") or "")
    if claimed_base_ref != base_ref:
        return "failure", (
            f"Candidate admission blocked: the pre-ready hostile review was taken against base branch "
            f"{claimed_base_ref!r}, not this pull request's {base_ref!r}."
        )
    return "success", verdict.reason


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

    ingress_state, ingress_message = verify_code_write_ingress_provenance(repository, token, head_sha, pr_number)
    if ingress_state != "success":
        return ingress_state, ingress_message

    review_state, review_message = verify_pre_ready_hostile_review(repository, token, head_sha, pr_number)
    if review_state != "success":
        return review_state, review_message

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
