"""Finding-driven remediation of Hunter-agent PRs (ADR 0039 L4, L6): minting, eligibility and promotion.

Control-domain only. The remediation authorization is the governing Issue's *live* claims plus one closed
``remediation`` group, signed with K_AUTH in the same ``hunter-issue-agent-signed-authorization-v2``
envelope, so it runs through the unchanged S3–S5 lifecycle (SPM/DPM compile, isolated executor,
credential-free validator, isolated publisher). Its TaskScope is the parent authorization's TaskScope
rebased onto the exact PR head; nothing in a finding can widen it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.issue_agent_execution import (
    ISSUE_AGENT_AUTHORIZATION_IDENTITY_PREFIX,
    ISSUE_AGENT_AUTHORIZATION_LABEL,
    ISSUE_AGENT_AUTHORIZATION_SIGNATURE_DOMAIN,
    ISSUE_AGENT_BASE_REF,
    ISSUE_AGENT_REMEDIATION_SCHEMA_VERSION,
    MAX_REMEDIATION_FINDINGS,
    IssueAgentAuthorizationError,
    IssueAgentRemediationAuthorization,
    SignedIssueAgentAuthorization,
    validate_remediation_group,
)
from hunter.task_scope import TaskScopeContract

#: Paths only trusted plumbing may change in a remediation commit (ADR 0039 L6).
PROMOTION_PATHS: Final = ("docs/DEFECT_REGISTRY.json", "docs/REVIEWER_FINDING_DISPOSITIONS.json")


class RemediationRefused(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def remediation_group(
    *,
    parent_authorization_id: str,
    issue_number: int,
    pull_request_number: int,
    bound_head_sha: str,
    attempt: int,
    findings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    digest = parent_authorization_id.partition(":")[2]
    group = {
        "parent_authorization_id": parent_authorization_id,
        "pull_request_number": pull_request_number,
        "branch": f"issue-{issue_number}-{digest[:16]}",
        "bound_head_sha": bound_head_sha,
        "attempt": attempt,
        "findings": sorted(
            ({"finding_id": f["finding_id"], "path": f["path"], "claim": f["claim"]} for f in findings),
            key=lambda item: item["finding_id"],
        )[:MAX_REMEDIATION_FINDINGS],
    }
    try:
        return validate_remediation_group(group, issue_number=issue_number)
    except IssueAgentAuthorizationError as error:
        raise RemediationRefused("NOT_ELIGIBLE", str(error)) from None


def remediation_authorization(
    issue: Mapping[str, Any], *, repository: str, owner_login: str, remediation: Mapping[str, Any]
) -> IssueAgentRemediationAuthorization:
    """The live-Issue gate (standing consent) plus the closed remediation group, with its derived identity."""

    labels = {str(label.get("name")) for label in issue.get("labels") or [] if isinstance(label, Mapping)}
    if issue.get("state") != "open" or "pull_request" in issue:
        raise RemediationRefused("ISSUE_CLOSED", "the governing Issue is no longer an open Issue")
    if ISSUE_AGENT_AUTHORIZATION_LABEL not in labels:
        raise RemediationRefused("OWNER_WITHDREW", "the owner removed the execution label (standing consent)")
    number = issue.get("number")
    if type(number) is not int or number < 1:
        raise RemediationRefused("NOT_ELIGIBLE", "the Issue number is malformed")
    claims = {
        "repository": repository,
        "issue_number": number,
        "issue_url": str(issue.get("html_url") or ""),
        "issue_title": str(issue.get("title") or ""),
        "issue_body": str(issue.get("body") or ""),
        "authorized_by": owner_login,
        "authorization_label": ISSUE_AGENT_AUTHORIZATION_LABEL,
        "issue_updated_at": str(issue.get("updated_at") or ""),
        "schema_version": ISSUE_AGENT_REMEDIATION_SCHEMA_VERSION,
        "remediation": dict(remediation),
    }
    digest = hashlib.sha256(_canonical(claims).encode("utf-8")).hexdigest()
    try:
        return IssueAgentRemediationAuthorization._from_mapping(
            {**claims, "authorization_id": f"{ISSUE_AGENT_AUTHORIZATION_IDENTITY_PREFIX}:{digest}"}
        )  # type: ignore[return-value]
    except IssueAgentAuthorizationError as error:
        raise RemediationRefused("NOT_ELIGIBLE", str(error)) from None


def remediation_scope(
    authorization: IssueAgentRemediationAuthorization, parent_task_scope: Mapping[str, Any]
) -> TaskScopeContract:
    """The parent's ledger-bound TaskScope, rebased onto the exact PR head; never wider.

    The promotion files are not added to the scope: the model may never write them (the validator refuses a
    result that touches ``PROMOTION_PATHS``), and only trusted plumbing adds them to the validated tree (L6).
    """

    assert authorization.remediation is not None
    return TaskScopeContract(
        task_id=authorization.authorization_id,
        branch_pattern=str(parent_task_scope["branch_pattern"]),
        base_ref=ISSUE_AGENT_BASE_REF,
        base_sha=authorization.remediation["bound_head_sha"],
        allowed_paths=tuple(parent_task_scope["allowed_paths"]),
        prohibited_paths=tuple(parent_task_scope["prohibited_paths"]),
    )


def sign_remediation(
    authorization: IssueAgentRemediationAuthorization, scope: TaskScopeContract, *, signing_key: Ed25519PrivateKey
) -> SignedIssueAgentAuthorization:
    message = ISSUE_AGENT_AUTHORIZATION_SIGNATURE_DOMAIN + _canonical(
        {"authorization": asdict(authorization), "implementation_scope": asdict(scope)}
    ).encode("utf-8")
    return SignedIssueAgentAuthorization(
        authorization=authorization,
        implementation_scope=scope,
        issuer_signature=signing_key.sign(message).hex(),
    )


__all__ = [
    "PROMOTION_PATHS",
    "RemediationRefused",
    "remediation_authorization",
    "remediation_group",
    "remediation_scope",
    "sign_remediation",
]
