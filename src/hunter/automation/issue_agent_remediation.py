"""Finding-driven remediation of Hunter-agent PRs (ADR 0039 L4, L6): minting, eligibility and promotion.

Control-domain only. The remediation authorization is the governing Issue's *live* claims plus one closed
``remediation`` group, signed with K_AUTH in the same ``hunter-issue-agent-signed-authorization-v2``
envelope, so it runs through the unchanged S3–S5 lifecycle (SPM/DPM compile, isolated executor,
credential-free validator, isolated publisher). Its TaskScope is the parent authorization's TaskScope
rebased onto the exact PR head; nothing in a finding can widen it.

The L6 promotion delta is a **pure function** of one closed promotion object and the canonical registry at
the reviewed head. The credential-free validator derives it and records its digest; the publisher re-derives
byte-identical bytes and compares, so neither needs the knowledge ledger and neither can be talked into a
different promotion. The model may never write either file.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation import issue_agent_replacement_executor as core
from hunter.automation.issue_agent_execution import (
    ISSUE_AGENT_AUTHORIZATION_IDENTITY_PREFIX,
    ISSUE_AGENT_AUTHORIZATION_LABEL,
    ISSUE_AGENT_AUTHORIZATION_SIGNATURE_DOMAIN,
    ISSUE_AGENT_BASE_REF,
    ISSUE_AGENT_REMEDIATION_SCHEMA_VERSION,
    MAX_REMEDIATION_FINDINGS,
    PROMOTION_PATHS,
    IssueAgentAuthorizationError,
    IssueAgentRemediationAuthorization,
    SignedIssueAgentAuthorization,
    validate_remediation_group,
)
from hunter.task_scope import TaskScopeContract

#: The canonical serialization of both governed promotion files, pinned so the delta is byte-deterministic.
PROMOTION_SERIALIZATION: Final = "json.dumps(document, indent=2, ensure_ascii=False) + newline"
_FAMILY = re.compile(r"DFF-([0-9]{3})")


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


# --- the L6 promotion delta (RD-3) -----------------------------------------------------------------------


class PromotionRefused(RuntimeError):
    """The recorded promotion object does not describe a delta this repository can promote."""


def _promotion_bytes(document: object) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def read_promotion_inputs(repo: Path, *, base_sha: str) -> dict[str, bytes]:
    """The two governed files exactly as they stand at the reviewed head, by trusted Git plumbing."""

    return {path: core.git_blob(repo, f"{base_sha}:{path}") for path in PROMOTION_PATHS}


def next_family_id(registry: Mapping[str, Any]) -> str:
    """The next ``DFF-nnn`` after every family the reviewed head already carries."""

    used = [
        int(match.group(1))
        for family in registry.get("families") or []
        if isinstance(family, Mapping) and (match := _FAMILY.fullmatch(str(family.get("id") or ""))) is not None
    ]
    if not used:
        raise PromotionRefused("the canonical registry carries no family identity")
    return f"DFF-{max(used) + 1:03d}"


def promote(
    *, repo: Path, group: Mapping[str, Any], proposal: Mapping[str, Any], finding: Mapping[str, Any]
) -> dict[str, bytes]:
    """ADR 0039 L6 (RD-3): the one trusted implementation both the validator and the publisher call.

    Every input is trusted. The validated result's ``proposal`` is only a choice between two closed shapes; the
    finding's provenance comes from the verified anchored knowledge ledger; and the canonical registry is read
    by Git plumbing at the reviewed head. The returned bytes name only ``PROMOTION_PATHS``, and the same inputs
    always produce the same bytes, so the publisher's independent derivation must match the validator's digest.
    """

    if proposal["finding_id"] != finding["finding_id"]:
        raise PromotionRefused("the proposal does not name the finding whose provenance was supplied")
    if int(group["pull_request_number"]) != int(finding["pull_request_number"]):
        raise PromotionRefused("the finding was not observed on the remediated pull request")
    disposition = dict(proposal["disposition"])
    guard = str(finding["path"])
    tests = sorted(str(item) for item in proposal["regression_tests"])
    entry: dict[str, Any] = {
        "finding_id": str(finding["finding_id"]),
        "guard": guard,
        "regression_tests": tests,
        "provenance": {
            "pull_request_number": int(finding["pull_request_number"]),
            "reviewed_head_sha": str(finding["reviewed_head_sha"]),
            "reviewer": str(finding["reviewer"]),
            "comment_id": int(finding["comment_id"]),
        },
    }
    if "family_id" in disposition:
        entry["family_id"] = str(disposition["family_id"])
    else:
        proposal_family = disposition["new_family"]
        entry["new_family"] = {
            "title": str(proposal_family["title"]),
            "invariant": str(proposal_family["invariant"]),
            "changed_paths": sorted({guard}),
        }
    return promotion_delta(repo, base_sha=str(group["bound_head_sha"]), entries=[entry])


def promotion_delta(repo: Path, *, base_sha: str, entries: Sequence[Mapping[str, Any]]) -> dict[str, bytes]:
    """ADR 0039 L6 (RD-3): the registry / finding-disposition delta for the given proven entries.

    A pure function of the entries and the canonical registry at the reviewed head, so the validator and the
    publisher produce byte-identical bytes. It appends one RFD record per proven finding and, for each, either
    extends an existing family's regression evidence or creates the smallest truthful ``DFF-<next>`` family. It
    names only ``PROMOTION_PATHS``, and it reads nothing a model wrote beyond the already-validated
    disposition's two closed shapes.
    """

    if not entries:
        raise PromotionRefused("a promotion delta must name at least one proven finding")
    if [item["finding_id"] for item in entries] != sorted({item["finding_id"] for item in entries}):
        raise PromotionRefused("the promotion entries are not unique and sorted by finding id")
    documents = read_promotion_inputs(repo, base_sha=base_sha)
    try:
        registry = json.loads(documents["docs/DEFECT_REGISTRY.json"])
        dispositions = json.loads(documents["docs/REVIEWER_FINDING_DISPOSITIONS.json"])
    except ValueError:
        raise PromotionRefused("the canonical promotion files are not JSON at the reviewed head") from None
    families = registry.get("families") if isinstance(registry, dict) else None
    findings = dispositions.get("findings") if isinstance(dispositions, dict) else None
    if not isinstance(families, list) or not isinstance(findings, list):
        raise PromotionRefused("the canonical promotion files do not carry the expected shapes")
    for entry in entries:
        _append_disposition(dispositions, findings, entry, _promote_family(registry, families, entry))
    return {
        "docs/DEFECT_REGISTRY.json": _promotion_bytes(registry),
        "docs/REVIEWER_FINDING_DISPOSITIONS.json": _promotion_bytes(dispositions),
    }


def _promote_family(registry: dict[str, Any], families: list[Any], entry: Mapping[str, Any]) -> str:
    """Extend a matched family's regression evidence, or append the smallest truthful new family.

    Returns the family identity this entry is promoted to, so the disposition record binds the same one.
    """

    tests = list(entry["regression_tests"])
    if "family_id" in entry:
        family_id = str(entry["family_id"])
        family = next((item for item in families if isinstance(item, Mapping) and item.get("id") == family_id), None)
        if not isinstance(family, dict):
            raise PromotionRefused(f"the proven family {family_id} does not exist at the reviewed head")
        evidence = [item for item in family.get("regression_evidence") or [] if isinstance(item, str)]
        family["regression_evidence"] = evidence + [item for item in tests if item not in evidence]
        return family_id
    proposal = entry["new_family"]
    family_id = next_family_id(registry)
    families.append(
        {
            "id": family_id,
            "title": str(proposal["title"]),
            "invariant": str(proposal["invariant"]),
            "applicability": {
                "changed_paths": sorted({str(item) for item in proposal["changed_paths"]}),
                "rationale": (
                    f"Proven by reviewer finding {entry['finding_id']} on PR "
                    f"{entry['provenance']['pull_request_number']} at "
                    f"{entry['provenance']['reviewed_head_sha'][:12]}: the guard surface is {entry['guard']}."
                ),
            },
            "prevention": {
                "mechanism": (
                    f"ADR 0039 L3.2 (RD-1): the invariant is proven by a RED->GREEN regression added for reviewer "
                    f"finding {entry['finding_id']} and enforced by the ordinary pre-push safety boundary."
                ),
                "boundary": "review",
            },
            "regression_evidence": tests,
            "lifecycle": "regression-tested",
            "sources": [
                f"PR #{entry['provenance']['pull_request_number']} {entry['provenance']['reviewer']} finding "
                f"{entry['finding_id']} (comment {entry['provenance']['comment_id']}) at "
                f"{entry['provenance']['reviewed_head_sha'][:12]}, remediated and proven under ADR 0039."
            ],
        }
    )
    return family_id


def _append_disposition(
    dispositions: dict[str, Any], findings: list[Any], entry: Mapping[str, Any], family_id: str
) -> None:
    """One canonical RFD record per proven finding: provenance, mapped family, guard and test."""

    provenance = entry["provenance"]
    family_title = str(entry.get("new_family", {}).get("title") or family_id)
    findings.append(
        {
            "id": f"RFD-{provenance['pull_request_number']}-{entry['finding_id'][:12]}",
            "source_provenance": {
                "reviewer": provenance["reviewer"],
                "pr_number": provenance["pull_request_number"],
                "reference": (
                    f"PR #{provenance['pull_request_number']} review comment {provenance['comment_id']} at "
                    f"head {provenance['reviewed_head_sha']}"
                ),
            },
            "validation_state": "validated",
            "classification": "new_systemic_defect" if "new_family" in entry else "recurrence",
            "mapped_defect_id": family_id,
            "mapped_defect_class": family_title,
            "resolution_state": "resolved",
            "permanent_disposition_evidence": (
                f"{entry['guard']}: proven by RED->GREEN regression {', '.join(entry['regression_tests'])} under "
                f"ADR 0039 L3.2 at remediated head evidence {provenance['reviewed_head_sha'][:12]}."
            ),
            "guard_reference": str(entry["guard"]),
            "test_reference": str(entry["regression_tests"][0]),
        }
    )


__all__ = [
    "PROMOTION_PATHS",
    "PROMOTION_SERIALIZATION",
    "PromotionRefused",
    "RemediationRefused",
    "next_family_id",
    "promote",
    "promotion_delta",
    "read_promotion_inputs",
    "remediation_authorization",
    "remediation_group",
    "remediation_scope",
    "sign_remediation",
]
