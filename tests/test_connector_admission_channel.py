"""Hostile tests for the Issue #409 connector-admission channel split.

Issues #403/#405 authorized the connector to write candidates, but admission
still demanded a verified local commit signature on every code-changing commit
in the range. The GitHub connector API creates commits server-side: there is no
local key and no pre-push boundary to sign at, so that requirement was
unsatisfiable by construction. On the first real governance-maintenance use --
Issue #402 / PR #408, head `eb9bc9a9` -- Governance Review blocked commit
`e3f062772d` as `unsigned` even though the candidate was correctly authorized on
`connector/issue-402-*` and carried its receipt.

The correction is a channel split, not a relaxation. Which channel wrote a
candidate is decided **first**, from trusted evidence, and only then is that
channel's proof required:

* an ordinary candidate proves ingress with a verified commit signature, exactly
  as before -- no receipt, no namespace, no exemption;
* a candidate proven connector-origin proves ingress with the connector
  channel's own evidence: the exact-head authorization receipt with every claim
  re-derived from trusted repository and pull-request evidence, one authorized
  writer committing the whole range, and the trusted hosted exact-head canonical
  preflight proof.

Connector evidence is never credited as local pre-push proof, and connector
origin is never something a candidate asserts -- it is the conclusion of the
trusted re-derivation, so a signature can neither establish it nor substitute
for any part of it.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import hunter_connector_write_ingress as ingress
import hunter_github_transport as transport
import hunter_governance_review_v2 as core
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _digest_for(path: str) -> str:
    """A deterministic stand-in for the blob SHA GitHub reports for `path`."""

    return hashlib.sha1(path.encode("utf-8")).hexdigest()  # noqa: S324


def _changes_for(paths: tuple[str, ...]) -> tuple[ingress.ConnectorFileChange, ...]:
    return tuple(ingress.ConnectorFileChange("modified", path, blob_sha=_digest_for(path)) for path in paths)


BASE_TIP = "a" * 40
STALE_BASE = "b" * 40
HEAD = "c" * 40

CONNECTOR = "connected-assistant"
CLONE_SIGNER = "claude"
ISSUE = "402"
CONNECTOR_BRANCH = "connector/issue-402-accept-adr-0036"
ORDINARY_BRANCH = "claude/issue-402-accept-adr-0036"
CONTENT_PATHS = ("docs/ADR/0036-source-handling-design-implementation-contract.md",)

PR_NUMBER = 408


def _policy(**overrides: object) -> ingress.ConnectorIngressPolicy:
    policy = ingress.ConnectorIngressPolicy(
        enabled=True,
        writers=((CONNECTOR, "feature-branch-write"), (CONNECTOR, "governance-maintenance")),
        required_capability="feature-branch-write",
        base_ref="main",
        forbidden_target_refs=frozenset({"main", "HEAD"}),
        branch_namespace="connector/",
        branch_pattern_template="connector/issue-{issue}-*",
        allowed_paths=("src/", "tests/", "docs/"),
        prohibited_paths=(".githooks/", ".github/", "scripts/", "docs/ADR/", "docs/CODE_WRITE_POLICY.json"),
        require_exact_base_tip=True,
        local_pre_push_equivalent=False,
        root_of_trust_paths=(".githooks/", ".github/", "scripts/", "docs/CODE_WRITE_POLICY.json"),
        additional_capabilities=(
            ingress.CapabilityScope(
                name="governance-maintenance",
                allowed_paths=("src/", "tests/", "docs/"),
                prohibited_paths=(".githooks/", ".github/", "scripts/", "docs/ADR/", "docs/CODE_WRITE_POLICY.json"),
                requires_issue_authorization=True,
            ),
        ),
        governance_scopes=(ingress.GovernanceScope(name="adr-lifecycle", unblocked_paths=("docs/ADR/",)),),
        issue_authorizations=(ingress.IssueAuthorization(issue=ISSUE, scopes=frozenset({"adr-lifecycle"})),),
    )
    return replace(policy, **overrides)  # type: ignore[arg-type]


def _authorization(
    *, grant: ingress.ConnectorIngressPolicy | None = None, **overrides: object
) -> ingress.ConnectorWriteAuthorization:
    base = grant if grant is not None else _policy()
    authorization = ingress.ConnectorWriteAuthorization(
        writer=CONNECTOR,
        capability="governance-maintenance",
        issue=ISSUE,
        base_ref="main",
        base_sha=BASE_TIP,
        target_ref=CONNECTOR_BRANCH,
        paths=CONTENT_PATHS,
        changes=_changes_for(CONTENT_PATHS),
        governance_scopes=("adr-lifecycle",),
        grant_fingerprint=base.fingerprint,
    )
    authorization = replace(authorization, **overrides)  # type: ignore[arg-type]
    if "changes" not in overrides:
        authorization = replace(authorization, changes=_changes_for(authorization.paths))
    return authorization


def _connector_commit(sha: str = HEAD, signer: str = CONNECTOR) -> dict:
    """A commit as the GitHub connector API creates it: server-side, unsigned."""
    return {
        "sha": sha,
        "committer": {"login": signer},
        "commit": {"verification": {"verified": False, "reason": "unsigned"}},
    }


def _signed_commit(sha: str = HEAD, signer: str = CLONE_SIGNER) -> dict:
    return {
        "sha": sha,
        "committer": {"login": signer},
        "commit": {"verification": {"verified": True, "reason": "valid"}},
    }


TRUSTED_RUN_ID = 211
HOSTED_PROOF = [
    {
        "id": 11,
        "context": core._upgrade_status_context(PR_NUMBER),
        "state": "success",
        "creator": {"login": core.TRUSTED_STATUS_CREATOR, "type": "Bot"},
        "target_url": f"https://github.com/fafa33/Project-Hunter/actions/runs/{TRUSTED_RUN_ID}",
    }
]
TRUSTED_UPGRADE_RUN = {
    "id": TRUSTED_RUN_ID,
    "name": core.TRUSTED_UPGRADE_WORKFLOW_NAME,
    "path": core.TRUSTED_UPGRADE_WORKFLOW_PATH,
    "event": "pull_request_target",
    "head_sha": HEAD,
    "status": "completed",
    "conclusion": "success",
    "pull_requests": [{"number": PR_NUMBER, "head": {"sha": HEAD}}],
}
SUCCESSFUL_BRANCH_RUN = {
    "head_sha": HEAD,
    "head_branch": CONNECTOR_BRANCH,
    "name": core.PRE_PR_WORKFLOW_NAME,
    "path": core.PRE_PR_WORKFLOW_PATH,
    "event": "push",
    "status": "completed",
    "conclusion": "success",
    "id": 100,
    "actor": {"login": CONNECTOR},
}


def _requested_head_sha(path: str) -> str:
    return path.split("head_sha=", 1)[1].split("&", 1)[0]


def _admission(
    monkeypatch: pytest.MonkeyPatch,
    *,
    commits: list[dict] | None = None,
    receipt: object | None = "default",
    head_ref: str = CONNECTOR_BRANCH,
    changed_paths: tuple[str, ...] = CONTENT_PATHS,
    changed_files: tuple[core.PullRequestFile, ...] | None = None,
    merge_base: str = BASE_TIP,
    statuses: list[dict] | None = None,
    policy: ingress.ConnectorIngressPolicy | None = None,
    head_policy: ingress.ConnectorIngressPolicy | None = None,
    connector_policy: tuple[bool, frozenset[str], str | None] | None = None,
    signers: frozenset[str] = frozenset({CLONE_SIGNER, "fafa33"}),
    push_actor: str = CONNECTOR,
    push_actors: dict[str, str] | None = None,
    missing_push_shas: frozenset[str] = frozenset(),
    push_runs: list[dict] | None = None,
    trusted_run: dict | None = None,
) -> tuple[str, str]:
    """Drive candidate_admission over stubbed trusted evidence."""

    grant = policy if policy is not None else _policy()
    head_grant = head_policy if head_policy is not None else grant
    monkeypatch.setattr(ingress, "load_policy", lambda *_a, **_k: (grant, ""))
    monkeypatch.setattr(ingress, "parse_policy", lambda *_a, **_k: (head_grant, ""))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_args: (
            True,
            (
                changed_files
                if changed_files is not None
                else tuple(core.PullRequestFile("modified", path, blob_sha=_digest_for(path)) for path in changed_paths)
            ),
            None,
        ),
    )
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, changed_paths, None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))
    monkeypatch.setattr(core, "load_ingress_provenance_policy", lambda: (signers, "", None))
    monkeypatch.setattr(
        core,
        "load_connector_write_ingress_policy",
        lambda: connector_policy if connector_policy is not None else (True, frozenset({CONNECTOR}), None),
    )

    document = _authorization().document() if receipt == "default" else receipt

    def fake_request(_repo, _token, _method, path, _payload=None):
        if f"pulls/{PR_NUMBER}/commits" in path:
            return commits if commits is not None else [_connector_commit()]
        if path.startswith(f"pulls/{PR_NUMBER}"):
            return {
                "head": {"ref": head_ref, "sha": HEAD},
                "base": {"ref": "main"},
                "user": {"login": CONNECTOR},
            }
        if path.startswith(f"contents/{core.CODE_WRITE_POLICY_RELATIVE_PATH}"):
            # `parse_policy` is stubbed above, so the bytes only have to decode.
            return {"content": base64.b64encode(b"{}").decode("ascii")}
        if path.startswith("contents/.hunter/"):
            if document is None:
                raise transport.GitHubRequestError("Not Found", category="permanent", status_code=404)
            return {"content": base64.b64encode(json.dumps(document).encode("utf-8")).decode("ascii")}
        if path.startswith("compare/"):
            return {"merge_base_commit": {"sha": merge_base}}
        if "statuses" in path:
            return statuses if statuses is not None else HOSTED_PROOF
        if path == f"actions/runs/{TRUSTED_RUN_ID}":
            return trusted_run if trusted_run is not None else TRUSTED_UPGRADE_RUN
        if "actions/runs" in path:
            requested_head = _requested_head_sha(path)
            if requested_head in missing_push_shas:
                return {"workflow_runs": []}
            runs = (
                push_runs
                if push_runs is not None
                else [
                    {
                        **SUCCESSFUL_BRANCH_RUN,
                        "head_sha": requested_head,
                        "head_branch": head_ref,
                        "actor": {"login": (push_actors or {}).get(requested_head, push_actor)},
                    }
                ]
            )
            return {"workflow_runs": runs}
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)
    return core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, PR_NUMBER)


# --------------------------------------------------------------------------
# 1. The defect: an authorized connector candidate is admissible unsigned
# --------------------------------------------------------------------------


def test_connector_candidate_is_admitted_without_a_local_commit_signature(monkeypatch) -> None:
    """The Issue #409 reproduction, inverted into the required behaviour."""
    state, description = _admission(monkeypatch)

    assert state == "success", description


def test_connector_admission_is_never_credited_as_local_pre_push_proof(monkeypatch) -> None:
    """The substitution is explicit and one-way, so nothing downstream may reuse it.

    `candidate_admission` reports the branch-preflight outcome on success, so the
    provenance verdict is asserted where it is produced.
    """
    captured: list[str] = []
    real = core.verify_code_write_ingress_provenance

    def recording(*args):
        ok, message = real(*args)
        captured.append(message)
        return ok, message

    monkeypatch.setattr(core, "verify_code_write_ingress_provenance", recording)
    state, _ = _admission(monkeypatch)

    assert state == "success"
    assert captured
    assert "Connector-origin range admitted on connector evidence" in captured[0]
    assert "this is not local pre-push proof" in captured[0]

    # The grant itself must still refuse to claim equivalence.
    policy, error = ingress.load_policy()
    assert error == ""
    assert policy is not None and policy.local_pre_push_equivalent is False


def test_a_whole_unsigned_connector_range_is_admitted_not_only_the_tip(monkeypatch) -> None:
    ancestor = "d" * 40
    state, description = _admission(
        monkeypatch,
        commits=[_connector_commit(ancestor), _connector_commit(HEAD)],
    )

    assert state == "success", description


# --------------------------------------------------------------------------
# 2. The clone-capable path is untouched
# --------------------------------------------------------------------------


def test_unsigned_ordinary_candidate_still_fails(monkeypatch) -> None:
    """No receipt and no connector namespace: the signature regime still applies."""
    state, description = _admission(
        monkeypatch,
        commits=[_connector_commit(signer=CLONE_SIGNER)],
        head_ref=ORDINARY_BRANCH,
        receipt=None,
    )

    assert state == "failure"
    assert "no verified pre-push ingress signature" in description


def test_signed_ordinary_candidate_still_passes(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        commits=[_signed_commit()],
        head_ref=ORDINARY_BRANCH,
        receipt=None,
    )

    assert state == "success", description


def test_unauthorized_signer_on_the_ordinary_path_still_fails(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        commits=[_signed_commit(signer="drive-by-account")],
        head_ref=ORDINARY_BRANCH,
        receipt=None,
    )

    assert state == "failure"
    assert "unauthorized ingress signer drive-by-account" in description


# --------------------------------------------------------------------------
# 3. A signed clone commit cannot masquerade as connector-origin
# --------------------------------------------------------------------------


def test_a_signed_clone_commit_cannot_claim_the_connector_channel(monkeypatch) -> None:
    """Connector origin is concluded from trusted evidence, never asserted.

    Here a clone-capable signer puts a properly signed commit on a connector
    branch. The receipt names the connector writer, so the writer binding rejects
    the range: taking the connector channel is not a way to bring foreign commits
    along, and being signed buys nothing there.
    """
    state, description = _admission(monkeypatch, commits=[_signed_commit(signer=CLONE_SIGNER)])

    assert state == "failure"
    assert f"the range carries commits from {CLONE_SIGNER}" in description


def test_a_receipt_naming_the_clone_signer_is_not_a_granted_writer(monkeypatch) -> None:
    """Rewriting the receipt to match the signer does not create a grant either."""
    state, description = _admission(
        monkeypatch,
        commits=[_signed_commit(signer=CLONE_SIGNER)],
        receipt=_authorization(writer=CLONE_SIGNER).document(),
    )

    assert state == "failure"
    assert "is not granted" in description


def test_a_receipt_outside_the_connector_namespace_is_refused(monkeypatch) -> None:
    """A receipt cannot pull an ordinary branch into the connector channel."""
    state, description = _admission(
        monkeypatch,
        commits=[_connector_commit()],
        head_ref=ORDINARY_BRANCH,
        receipt=_authorization(target_ref=ORDINARY_BRANCH).document(),
    )

    assert state == "failure"
    assert "outside the connector namespace" in description


def test_connector_writer_must_be_bound_by_the_trusted_grant(monkeypatch) -> None:
    """The trusted default-branch grant, not the receipt, decides who may write."""
    state, description = _admission(monkeypatch, connector_policy=(True, frozenset({"someone-else"}), None))

    assert state == "failure"
    assert "is not bound by the trusted connector grant" in description


def test_connector_origin_is_refused_while_the_grant_is_inactive(monkeypatch) -> None:
    state, description = _admission(monkeypatch, connector_policy=(False, frozenset(), None))

    assert state == "failure"
    assert "the connector write ingress is not active in trusted default-branch state" in description


# --------------------------------------------------------------------------
# 3b. Credential impersonation and post-authorization content mutation
#     (Issue #409 review, both P1)
# --------------------------------------------------------------------------


def test_committer_metadata_alone_cannot_impersonate_the_granted_writer(monkeypatch) -> None:
    """The first P1: committer name/email on an unsigned commit is caller-supplied.

    A different write-capable credential can set the granted writer's committer
    identity on every commit and mint the deterministic receipt for that writer.
    The exact-head push workflow still records the credential that pushed it.
    """
    state, description = _admission(
        monkeypatch,
        commits=[_connector_commit(signer=CONNECTOR)],
        push_actor="impersonator",
    )

    assert state == "failure"
    assert "commit cccccccccc was pushed by authenticated actor 'impersonator'" in description


def test_the_authenticated_push_actor_must_be_the_receipt_writer_not_merely_granted(monkeypatch) -> None:
    """Another *granted* account is still not this receipt's writer."""
    grant = _policy(writers=_policy().writers + (("second-writer", "governance-maintenance"),))

    state, description = _admission(
        monkeypatch,
        policy=grant,
        head_policy=grant,
        receipt=_authorization(grant=grant).document(),
        push_actor="second-writer",
    )

    assert state == "failure"
    assert "commit cccccccccc was pushed by authenticated actor 'second-writer'" in description


def test_same_head_push_on_another_branch_is_not_writer_evidence_for_this_pr(monkeypatch) -> None:
    wrong_branch_run = {**SUCCESSFUL_BRANCH_RUN, "head_branch": "connector/issue-999-other"}

    state, description = _admission(monkeypatch, push_runs=[wrong_branch_run])

    assert state == "failure"
    assert "authenticated push actor evidence for commit cccccccccc is unavailable" in description


def test_mixed_writer_range_fails_even_when_the_exact_head_actor_is_authorized(monkeypatch) -> None:
    ancestor = "d" * 40
    state, description = _admission(
        monkeypatch,
        commits=[_connector_commit(ancestor, signer="other-writer"), _connector_commit()],
    )

    assert state == "failure"
    assert "range carries commits from other-writer" in description


def test_forged_authorized_committer_cannot_hide_a_foreign_ancestor_push(monkeypatch) -> None:
    ancestor = "d" * 40
    state, description = _admission(
        monkeypatch,
        commits=[_connector_commit(ancestor), _connector_commit()],
        push_actors={ancestor: "other-writer", HEAD: CONNECTOR},
    )

    assert state == "failure"
    assert f"commit {ancestor[:10]} was pushed by authenticated actor 'other-writer'" in description


def test_connector_ancestor_without_authenticated_push_evidence_fails(monkeypatch) -> None:
    ancestor = "d" * 40
    state, description = _admission(
        monkeypatch,
        commits=[_connector_commit(ancestor), _connector_commit()],
        missing_push_shas=frozenset({ancestor}),
    )

    assert state == "failure"
    assert f"authenticated push actor evidence for commit {ancestor[:10]} is unavailable" in description


def test_content_mutated_after_authorization_on_an_authorized_path_fails(monkeypatch) -> None:
    """The second P1: the receipt binds the exact bytes, not just the path set.

    Same branch, same base, same changed-file set, same writer -- only the content
    of an already-authorized path differs from what was authorized. Before this
    binding the re-derivation still succeeded and the later head was admitted with
    the stale receipt once hosted preflight passed.
    """
    mutated = tuple(core.PullRequestFile("modified", path, blob_sha="9" * 40) for path in CONTENT_PATHS)
    state, description = _admission(monkeypatch, changed_files=mutated)

    assert state == "failure"
    assert "exact file operations or resulting content do not match" in description


def test_a_receipt_covering_no_content_cannot_admit_an_authorized_path(monkeypatch) -> None:
    """An empty change set is not "nothing to check": it authorizes no content."""
    state, description = _admission(monkeypatch, receipt=_authorization(changes=()).document())

    assert state == "failure"
    assert "exact file operations or resulting content do not match" in description


def test_deletion_cannot_reuse_the_base_blob_sha_as_authorized_content(monkeypatch) -> None:
    path = CONTENT_PATHS[0]
    deleted = (core.PullRequestFile("removed", path, blob_sha=""),)
    stale_content = (ingress.ConnectorFileChange("modified", path, blob_sha=_digest_for(path)),)

    state, description = _admission(
        monkeypatch,
        changed_files=deleted,
        receipt=_authorization(changes=stale_content).document(),
    )

    assert state == "failure"
    assert "exact file operations or resulting content do not match" in description


def test_deletion_is_authorized_as_absence(monkeypatch) -> None:
    path = CONTENT_PATHS[0]
    deletion = (ingress.ConnectorFileChange("removed", path),)
    changed = (core.PullRequestFile("removed", path),)

    state, description = _admission(
        monkeypatch,
        changed_files=changed,
        receipt=_authorization(changes=deletion).document(),
    )

    assert state == "success", description


def test_rename_from_root_of_trust_to_allowed_destination_fails(monkeypatch) -> None:
    source = ".github/workflows/hunter-governance-review.yml"
    destination = "docs/copied-governance-review.yml"
    change = ingress.ConnectorFileChange("renamed", destination, source, _digest_for(destination))
    changed = (core.PullRequestFile("renamed", destination, source, _digest_for(destination)),)

    state, description = _admission(
        monkeypatch,
        changed_paths=(source, destination),
        changed_files=changed,
        receipt=_authorization(paths=(source, destination), changes=(change,)).document(),
    )

    assert state == "failure"
    assert "root-of-trust" in description
    assert source in description


def test_rename_cannot_hide_a_source_transition_inside_the_receipt_path(monkeypatch) -> None:
    source = "docs/old.md"
    destination = ingress.AUTHORIZATION_RECEIPT_PATH
    changed = (core.PullRequestFile("renamed", destination, source, _digest_for(destination)),)

    state, description = _admission(
        monkeypatch,
        changed_paths=(source, destination),
        changed_files=changed,
        receipt=_authorization(paths=(), changes=()).document(),
    )

    assert state == "failure"
    assert "receipt path may not be deleted, renamed" in description


def test_rename_plus_modify_requires_the_exact_destination_blob(monkeypatch) -> None:
    source = "docs/old.md"
    destination = "docs/new.md"
    authorized = ingress.ConnectorFileChange("renamed", destination, source, _digest_for(destination))
    mutated = core.PullRequestFile("renamed", destination, source, "9" * 40)

    state, description = _admission(
        monkeypatch,
        changed_paths=(source, destination),
        changed_files=(mutated,),
        receipt=_authorization(paths=(source, destination), changes=(authorized,)).document(),
    )

    assert state == "failure"
    assert "exact file operations or resulting content do not match" in description


def test_addition_cannot_replay_a_modification_authorization(monkeypatch) -> None:
    path = CONTENT_PATHS[0]
    changed = (core.PullRequestFile("added", path, blob_sha=_digest_for(path)),)

    state, description = _admission(monkeypatch, changed_files=changed)

    assert state == "failure"
    assert "exact file operations or resulting content do not match" in description


def test_exact_changes_are_part_of_the_authorization_identity(monkeypatch) -> None:
    """Editing a change in a receipt breaks its own identifier."""
    document = _authorization().document()
    document["claims"]["changes"][0]["blob_sha"] = "9" * 40

    state, description = _admission(monkeypatch, receipt=document)

    assert state == "failure"
    assert "does not match its own claims" in description


def test_a_write_request_must_declare_a_change_for_every_authorized_path() -> None:
    """The write authorizer refuses to mint a receipt that binds no operation."""
    policy = _policy()
    request = ingress.ConnectorWriteRequest(
        writer=CONNECTOR,
        capability="governance-maintenance",
        issue=ISSUE,
        target_ref=f"refs/heads/{CONNECTOR_BRANCH}",
        base_ref="main",
        base_sha=BASE_TIP,
        paths=CONTENT_PATHS,
    )

    decision = ingress.evaluate_write_request(request, policy, resolve_base_tip=lambda _ref: BASE_TIP)

    assert decision.authorized is False
    assert "exact, unambiguous change semantics" in decision.reason


def test_the_authorizer_binds_the_changes_it_was_given() -> None:
    changes = _changes_for(CONTENT_PATHS)
    request = ingress.ConnectorWriteRequest(
        writer=CONNECTOR,
        capability="governance-maintenance",
        issue=ISSUE,
        target_ref=f"refs/heads/{CONNECTOR_BRANCH}",
        base_ref="main",
        base_sha=BASE_TIP,
        paths=CONTENT_PATHS,
        changes=changes,
    )

    decision = ingress.evaluate_write_request(request, _policy(), resolve_base_tip=lambda _ref: BASE_TIP)

    assert decision.authorized is True, decision.reason
    assert decision.authorization is not None
    assert decision.authorization.changes == changes


def test_the_bound_digest_is_the_git_blob_sha_github_reports() -> None:
    """The two sides must hash the same way or the binding is theatre."""
    content = b"# ADR 0036\n\nStatus: Accepted\n"
    import subprocess

    expected = subprocess.run(
        ("git", "hash-object", "--stdin"), input=content, capture_output=True, check=True
    ).stdout.decode()

    assert ingress.blob_digest(content) == expected.strip()


# --------------------------------------------------------------------------
# 4. Connector-namespace candidate without valid receipt evidence
# --------------------------------------------------------------------------


def test_connector_branch_without_a_receipt_fails(monkeypatch) -> None:
    state, description = _admission(monkeypatch, receipt=None)

    assert state == "failure"
    assert "carries no exact-head authorization receipt" in description


def test_forged_receipt_fails(monkeypatch) -> None:
    document = _authorization().document()
    document["claims"]["paths"] = ["docs/ADR/other.md"]

    state, description = _admission(monkeypatch, receipt=document)

    assert state == "failure"
    assert "does not match its own claims" in description


def test_wrong_issue_receipt_fails(monkeypatch) -> None:
    state, description = _admission(monkeypatch, receipt=_authorization(issue="390").document())

    assert state == "failure"
    assert "claims Issue #390 but the branch binds Issue #402" in description


def test_wrong_branch_receipt_fails(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        receipt=_authorization(target_ref="connector/issue-402-other-work").document(),
    )

    assert state == "failure"
    assert "is bound to branch" in description


def test_wrong_path_receipt_fails(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        changed_paths=CONTENT_PATHS + ("docs/architecture-index.md",),
    )

    assert state == "failure"
    assert "do not match the authorized path set" in description


def test_stale_base_receipt_fails(monkeypatch) -> None:
    state, description = _admission(monkeypatch, merge_base=STALE_BASE)

    assert state == "failure"
    assert "is not this candidate's fork point" in description


def test_receipt_minted_under_an_older_grant_fails(monkeypatch) -> None:
    older = _policy(issue_authorizations=())

    state, description = _admission(monkeypatch, receipt=_authorization(grant=older).document())

    assert state == "failure"
    assert "re-authorize against the current grant" in description


def test_root_of_trust_path_fails_even_on_the_connector_channel(monkeypatch) -> None:
    paths = CONTENT_PATHS + ("scripts/hunter_governance_review_v2.py",)

    state, description = _admission(
        monkeypatch,
        changed_paths=paths,
        receipt=_authorization(paths=paths).document(),
    )

    assert state == "failure"
    assert "root-of-trust path(s) no capability on this ingress may write" in description


def test_direct_main_target_is_unrepresentable_on_the_connector_channel(monkeypatch) -> None:
    """`main` is neither in the namespace nor a branch the pattern can bind."""
    state, description = _admission(
        monkeypatch,
        head_ref="main",
        receipt=_authorization(target_ref="main").document(),
    )

    assert state == "failure"
    assert "outside the connector namespace" in description


# --------------------------------------------------------------------------
# 5. The trusted hosted exact-head proof stays mandatory
# --------------------------------------------------------------------------


def test_connector_candidate_without_hosted_exact_head_proof_fails(monkeypatch) -> None:
    """The one proof no writer on any channel can mint is what replaces the signature."""
    state, description = _admission(monkeypatch, statuses=[])

    assert state == "failure"
    assert "requires trusted hosted exact-head canonical preflight proof" in description


def test_hosted_proof_bound_to_another_pr_does_not_admit_a_connector_candidate(monkeypatch) -> None:
    other = [{"id": 11, "context": core._upgrade_status_context(999), "state": "success"}]

    state, description = _admission(monkeypatch, statuses=other)

    assert state == "failure"
    assert "requires trusted hosted exact-head canonical preflight proof" in description


def test_failed_hosted_proof_does_not_admit_a_connector_candidate(monkeypatch) -> None:
    failed = [{"id": 11, "context": core._upgrade_status_context(PR_NUMBER), "state": "failure"}]

    state, description = _admission(monkeypatch, statuses=failed)

    assert state == "failure"
    assert "requires trusted hosted exact-head canonical preflight proof" in description


def test_push_capable_user_cannot_forge_the_canonical_status_context(monkeypatch) -> None:
    forged = [{**HOSTED_PROOF[0], "creator": {"login": "fafa33", "type": "User"}}]

    state, description = _admission(monkeypatch, statuses=forged)

    assert state == "failure"
    assert "untrusted publisher" in description


def test_wrong_github_app_cannot_publish_the_canonical_proof(monkeypatch) -> None:
    forged = [{**HOSTED_PROOF[0], "creator": {"login": "other-app[bot]", "type": "Bot"}}]

    state, description = _admission(monkeypatch, statuses=forged)

    assert state == "failure"
    assert "untrusted publisher" in description


@pytest.mark.parametrize(
    "run",
    [
        {**TRUSTED_UPGRADE_RUN, "name": "Wrong Workflow"},
        {
            **TRUSTED_UPGRADE_RUN,
            "pull_requests": [{"number": PR_NUMBER + 1, "head": {"sha": HEAD}}],
        },
        {
            **TRUSTED_UPGRADE_RUN,
            "head_sha": "f" * 40,
            "pull_requests": [{"number": PR_NUMBER, "head": {"sha": "f" * 40}}],
        },
    ],
    ids=("wrong-workflow", "wrong-pr", "wrong-head"),
)
def test_status_proof_must_resolve_to_the_exact_trusted_workflow_pr_and_head(monkeypatch, run: dict) -> None:
    state, description = _admission(monkeypatch, trusted_run=run)

    assert state == "failure"
    assert "trusted hosted exact-head canonical preflight proof" in description


# --------------------------------------------------------------------------
# 6. Same-PR escalation remains impossible on the connector channel
# --------------------------------------------------------------------------


def test_same_pr_grant_escalation_still_fails(monkeypatch) -> None:
    """A head that widens the grant is refused before the channel buys anything."""
    trusted = _policy(issue_authorizations=())
    widened = _policy()

    state, description = _admission(
        monkeypatch,
        policy=trusted,
        head_policy=widened,
        receipt=_authorization(grant=trusted).document(),
    )

    assert state == "failure"
    assert "may not widen the grant it is authorized under" in description


def test_same_pr_policy_write_still_fails(monkeypatch) -> None:
    """Writing the grant itself stays a root-of-trust violation on this channel."""
    paths = CONTENT_PATHS + ("docs/CODE_WRITE_POLICY.json",)

    state, description = _admission(
        monkeypatch,
        changed_paths=paths,
        receipt=_authorization(paths=paths).document(),
    )

    assert state == "failure"
    assert "root-of-trust path(s) no capability on this ingress may write" in description


# --------------------------------------------------------------------------
# The channel decision itself
# --------------------------------------------------------------------------


def test_connector_origin_is_a_conclusion_not_a_candidate_claim() -> None:
    """`ConnectorAdmission.origin` is only ever set by the trusted re-derivation."""
    ordinary = core.ConnectorAdmission(ok=True, origin=False, message="")
    blocked = core.ConnectorAdmission(ok=False, origin=False, message="blocked")

    assert ordinary.ok and not ordinary.origin and ordinary.writer == ""
    assert not blocked.ok and not blocked.origin


def test_a_commit_with_no_committer_identity_blocks_a_connector_range(monkeypatch) -> None:
    """Unidentifiable is not absent: it names itself unknown and still blocks."""
    anonymous = {"sha": "d" * 40, "commit": {"verification": {"verified": False, "reason": "unsigned"}}}

    state, description = _admission(monkeypatch, commits=[anonymous, _connector_commit()])

    assert state == "failure"
    assert "the range carries commits from unknown" in description


def test_committer_identity_comes_from_the_trusted_listing_not_signature_results(monkeypatch) -> None:
    """Deriving identity from signatures would make connector origin unprovable.

    An unsigned connector commit contributes no "attested signer", so a writer
    binding fed from signature results would see an empty set and could never
    reject a foreign committer on the connector channel. It is fed from the
    trusted commit listing instead, which is why the foreign-committer case above
    fails rather than silently passing.
    """
    foreign = "d" * 40
    state, description = _admission(
        monkeypatch,
        commits=[_connector_commit(foreign, signer="someone-else"), _connector_commit(HEAD)],
    )

    assert state == "failure"
    assert "the range carries commits from someone-else" in description
