"""ADR 0039 L4: the remediation authorization (minting, identity, target, task text)."""

from __future__ import annotations

import copy
import dataclasses
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from hunter.automation import issue_agent_remediation as remediation
from hunter.automation.issue_agent_execution import (
    IssueAgentAuthorization,
    IssueAgentAuthorizationError,
    IssueAgentAuthorizationVerifier,
    IssueAgentRemediationAuthorization,
    SignedIssueAgentAuthorization,
    derive_execution_target,
    issue_agent_task_text,
    verify_signed_authorization,
)

KEY = Ed25519PrivateKey.generate()
VERIFIER = IssueAgentAuthorizationVerifier(KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
REPOSITORY, OWNER = "fafa33/Project-Hunter", "fafa33"
PARENT = "hunter-issue-agent-authorization:" + "a" * 64
BRANCH = f"issue-520-{'a' * 16}"
HEAD = "c" * 40
FINDING = {"finding_id": "1" * 64, "path": "src/hunter/x.py", "claim": "require a successful preflight first."}
PARENT_SCOPE = {
    "task_id": PARENT,
    "branch_pattern": "issue-520-*",
    "base_ref": "main",
    "base_sha": "b" * 40,
    "allowed_paths": ["src/hunter/"],
    "prohibited_paths": ["src/hunter/secrets/"],
}


def issue(**changes: Any) -> dict[str, Any]:
    value = {
        "number": 520,
        "state": "open",
        "html_url": "https://github.com/fafa33/Project-Hunter/issues/520",
        "title": "Canary",
        "body": "Create docs/ISSUE_AGENT_CANARY.md.",
        "updated_at": "2026-10-04T10:00:00Z",
        "labels": [{"name": "hunter-agent-execute"}],
    }
    value.update(changes)
    return value


def group(**changes: Any) -> dict[str, Any]:
    arguments = dict(
        parent_authorization_id=PARENT,
        issue_number=520,
        pull_request_number=600,
        bound_head_sha=HEAD,
        attempt=1,
        findings=[FINDING],
    )
    arguments.update(changes)
    return remediation.remediation_group(**arguments)


def minted(**issue_changes: Any) -> IssueAgentRemediationAuthorization:
    return remediation.remediation_authorization(
        issue(**issue_changes), repository=REPOSITORY, owner_login=OWNER, remediation=group()
    )


def signed() -> SignedIssueAgentAuthorization:
    authorization = minted()
    return remediation.sign_remediation(
        authorization, remediation.remediation_scope(authorization, PARENT_SCOPE), signing_key=KEY
    )


def test_a_remediation_runs_on_the_parent_branch_at_the_exact_pr_head() -> None:
    document = signed()
    target = derive_execution_target(document)
    assert (target.branch, target.base_sha) == (BRANCH, HEAD)
    assert document.implementation_scope.allowed_paths == ("src/hunter/",)
    assert document.implementation_scope.prohibited_paths == ("src/hunter/secrets/",)


def test_the_signed_document_round_trips_and_verifies_as_the_owner_consent() -> None:
    document = SignedIssueAgentAuthorization.from_json(signed().to_json())
    assert isinstance(document.authorization, IssueAgentRemediationAuthorization)
    assert verify_signed_authorization(document, issuer_verifier=VERIFIER, repository=REPOSITORY, owner_login=OWNER)


def test_the_identity_covers_the_remediation_group() -> None:
    base = minted().authorization_id
    other = remediation.remediation_authorization(
        issue(), repository=REPOSITORY, owner_login=OWNER, remediation=group(attempt=2)
    )
    moved = remediation.remediation_authorization(
        issue(), repository=REPOSITORY, owner_login=OWNER, remediation=group(bound_head_sha="d" * 40)
    )
    assert len({base, other.authorization_id, moved.authorization_id}) == 3


def test_a_tampered_remediation_group_breaks_the_identity_and_the_signature() -> None:
    document = signed()
    payload = copy.deepcopy(SignedIssueAgentAuthorization.from_json(document.to_json()).to_json())
    forged = payload.replace(HEAD, "d" * 40)
    with pytest.raises(IssueAgentAuthorizationError):
        SignedIssueAgentAuthorization.from_json(forged)


def test_the_issue_v1_document_is_unchanged() -> None:
    claims = {
        "repository": REPOSITORY, "issue_number": 520, "issue_url": "u", "issue_title": "t", "issue_body": "b",
        "authorized_by": OWNER, "authorization_label": "hunter-agent-execute", "issue_updated_at": "x",
        "schema_version": "hunter-issue-agent-authorization-v1",
    }  # fmt: skip
    import hashlib
    import json

    digest = hashlib.sha256(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    parsed = IssueAgentAuthorization._from_mapping(
        {**claims, "authorization_id": f"hunter-issue-agent-authorization:{digest}"}
    )
    assert type(parsed) is IssueAgentAuthorization
    with pytest.raises(IssueAgentAuthorizationError, match="schema mismatch"):
        IssueAgentAuthorization._from_mapping({**claims, "authorization_id": "x", "remediation": group()})


def test_the_task_is_bounded_to_the_findings() -> None:
    import json

    text = json.loads(issue_agent_task_text(minted()))
    assert text["remediation_of_pull_request"] == 600
    assert text["review_findings"] == [{"path": FINDING["path"], "claim": FINDING["claim"]}]


@pytest.mark.parametrize(
    ("changes", "code"),
    [({"state": "closed"}, "ISSUE_CLOSED"), ({"pull_request": {}}, "ISSUE_CLOSED"), ({"labels": []}, "OWNER_WITHDREW")],
)
def test_the_live_issue_is_the_standing_consent(changes: dict, code: str) -> None:
    with pytest.raises(remediation.RemediationRefused, match=code):
        minted(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"parent_authorization_id": "not-an-id"},
        {"bound_head_sha": "short"},
        {"attempt": 0},
        {"pull_request_number": 0},
        {"findings": []},
        {"findings": [dict(FINDING, claim="")]},
        {"findings": [dict(FINDING, path="../etc/passwd x")]},
        {"findings": [FINDING, FINDING]},
    ],
)
def test_a_malformed_remediation_group_is_not_eligible(changes: dict) -> None:
    with pytest.raises(remediation.RemediationRefused, match="NOT_ELIGIBLE"):
        group(**changes)


def test_a_remediation_cannot_target_another_branch_or_base() -> None:
    authorization = minted()
    scope = remediation.remediation_scope(authorization, PARENT_SCOPE)
    wrong_base = SignedIssueAgentAuthorization(
        authorization=authorization,
        implementation_scope=dataclasses.replace(scope, base_sha="e" * 40),
        issuer_signature="0" * 128,
    )
    with pytest.raises(IssueAgentAuthorizationError, match="exact PR head"):
        derive_execution_target(wrong_base)
    bad = group()
    bad["branch"] = "issue-520-" + "f" * 16
    with pytest.raises(IssueAgentAuthorizationError, match="parent authorization's branch"):
        IssueAgentRemediationAuthorization._from_mapping(
            {**minted().canonical_claims, "remediation": bad, "authorization_id": minted().authorization_id}
        )
