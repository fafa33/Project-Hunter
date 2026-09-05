"""Deterministic hostile tests for the Issue #405 governance-maintenance capability.

Issue #403 gave the connected assistant an ordinary `feature-branch-write`
capability and deliberately closed every governance-sensitive path, which turned
owner-authorized ADR lifecycle work into a wait for a clone-capable external
agent. Issue #405 opens that work as a *second capability on the same ingress*
without opening the guardrails themselves.

Four boundaries are covered here:

* the **root-of-trust floor** -- the files that decide whether a candidate may be
  written and admitted at all. No capability may write them, no named scope may
  unblock them, and the floor is derived from the canonical gate chain rather
  than restated, so it cannot fall behind the authority it protects;
* **per-Issue authorization** -- governance maintenance grants nothing until the
  owner authorizes a governing Issue for named scopes on the trusted default
  branch, so a branch for an unauthorized Issue, or an authorized Issue reaching
  outside its scopes, fails closed;
* **grant version pinning** -- the receipt pins a fingerprint over the exact
  grant it was authorized under, and the trusted controller re-derives that
  fingerprint from the default branch, so a receipt minted under any other
  version of the grant is stale;
* **same-candidate self-escalation** -- a candidate may propose a wider grant,
  which is what a governed pull request is for, but the controller compares the
  candidate head's grant with the trusted one and refuses to let the proposal be
  in force for its own pull request.

The ordinary Issue #403 write path is exercised here too, on the shipped grant,
because "governance maintenance was added" must not quietly mean "ordinary
connector writes changed".
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import hunter_connector_write_ingress as ingress
import hunter_defect_prevention_preflight as prevention
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
GOVERNANCE_ISSUE = "402"
GOVERNANCE_BRANCH = "connector/issue-402-accept-adr-0036"
ORDINARY_BRANCH = "connector/issue-403-example"
ADR_PATHS = (
    "docs/ADR/0036-source-handling-design-implementation-contract.md",
    "docs/architecture-index.md",
)
ORDINARY_PATHS = ("src/hunter/example.py", "tests/test_example.py")

ROOT_OF_TRUST = (
    ".githooks/",
    ".github/workflows/",
    "scripts/hunter_pr_preflight.py",
    "scripts/hunter_connector_write_ingress.py",
    "scripts/hunter_governance_review_v2.py",
    "scripts/hunter_workflow_state.py",
    "pyproject.toml",
    "docs/CODE_WRITE_POLICY.json",
)
CLOSED_BY_DEFAULT = (
    ".githooks/",
    ".github/",
    "scripts/",
    "build_backend/",
    "requirements/",
    "pyproject.toml",
    "docs/ADR/",
    "docs/architecture-index.md",
    "docs/CODE_WRITE_POLICY.json",
    "docs/DEFECT_REGISTRY.json",
)


def _tip(sha: str = BASE_TIP):
    """A stand-in for trusted repository state, never for caller input."""

    def resolver(_base_ref: str) -> str:
        return sha

    return resolver


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
        prohibited_paths=CLOSED_BY_DEFAULT,
        require_exact_base_tip=True,
        local_pre_push_equivalent=False,
        root_of_trust_paths=ROOT_OF_TRUST,
        additional_capabilities=(
            ingress.CapabilityScope(
                name="governance-maintenance",
                allowed_paths=("src/", "tests/", "docs/", "scripts/"),
                prohibited_paths=CLOSED_BY_DEFAULT,
                requires_issue_authorization=True,
            ),
        ),
        governance_scopes=(
            ingress.GovernanceScope(
                name="adr-lifecycle",
                unblocked_paths=("docs/ADR/", "docs/architecture-index.md"),
            ),
            ingress.GovernanceScope(
                name="defect-registry",
                unblocked_paths=("docs/DEFECT_REGISTRY.json",),
            ),
        ),
        issue_authorizations=(ingress.IssueAuthorization(issue=GOVERNANCE_ISSUE, scopes=frozenset({"adr-lifecycle"})),),
    )
    return replace(policy, **overrides)  # type: ignore[arg-type]


def _request(**overrides: object) -> ingress.ConnectorWriteRequest:
    request = ingress.ConnectorWriteRequest(
        writer=CONNECTOR,
        capability="governance-maintenance",
        issue=GOVERNANCE_ISSUE,
        target_ref=f"refs/heads/{GOVERNANCE_BRANCH}",
        base_ref="main",
        base_sha=BASE_TIP,
        paths=ADR_PATHS,
    )
    request = replace(request, **overrides)  # type: ignore[arg-type]
    if "changes" not in overrides:
        request = replace(request, changes=_changes_for(request.paths))
    return request


def _evaluate(
    *, tip: str = BASE_TIP, policy: ingress.ConnectorIngressPolicy | None = None, **overrides: object
) -> ingress.IngressDecision:
    return ingress.evaluate_write_request(
        _request(**overrides),
        policy if policy is not None else _policy(),
        resolve_base_tip=_tip(tip),
    )


# --------------------------------------------------------------------------
# Write authorization: the owner-authorized ADR lifecycle path works ...
# --------------------------------------------------------------------------


def test_owner_authorized_adr_lifecycle_maintenance_is_authorized() -> None:
    """The whole point: Issue #402's ADR lifecycle work needs no external agent."""
    decision = _evaluate()

    assert decision.authorized is True, decision.reason
    assert decision.authorization is not None
    assert decision.authorization.capability == "governance-maintenance"
    assert decision.authorization.governance_scopes == ("adr-lifecycle",)
    assert decision.authorization.grant_fingerprint == _policy().fingerprint


def test_governance_scopes_are_derived_from_the_manifest_not_declared_by_the_caller() -> None:
    """The request carries no scope field, so a caller cannot name its own scopes."""
    assert "governance_scopes" not in {field for field in vars(_request())}

    decision = _evaluate()

    assert decision.authorization is not None
    assert decision.authorization.governance_scopes == ("adr-lifecycle",)


def test_ordinary_feature_branch_write_still_works_under_the_extended_grant() -> None:
    """Issue #403's path is unchanged: same capability, same scope, same outcome."""
    decision = _evaluate(
        capability="feature-branch-write",
        issue="403",
        target_ref=f"refs/heads/{ORDINARY_BRANCH}",
        paths=ORDINARY_PATHS,
    )

    assert decision.authorized is True, decision.reason
    assert decision.authorization is not None
    assert decision.authorization.capability == "feature-branch-write"
    assert decision.authorization.governance_scopes == ()


def test_ordinary_capability_still_cannot_reach_governance_paths() -> None:
    """Governance maintenance is a separate capability, never a wider default."""
    decision = _evaluate(capability="feature-branch-write", paths=ADR_PATHS)

    assert decision.authorized is False
    assert "prohibited path(s)" in decision.reason
    assert "docs/ADR/" in decision.reason


# --------------------------------------------------------------------------
# ... and every hostile variant of it fails closed
# --------------------------------------------------------------------------


def test_governance_maintenance_for_an_unauthorized_issue_is_rejected() -> None:
    """Wrong Issue: the capability grants nothing without an owner authorization."""
    decision = _evaluate(issue="390", target_ref="refs/heads/connector/issue-390-composition-root")

    assert decision.authorized is False
    assert "no owner-authored governance-maintenance authorization" in decision.reason


def test_authorized_issue_cannot_reach_outside_its_authorized_scopes() -> None:
    """Issue #402 holds adr-lifecycle only, so a defect-registry path stays closed."""
    decision = _evaluate(paths=("docs/DEFECT_REGISTRY.json",))

    assert decision.authorized is False
    assert "outside the governance scope authorized for Issue #402" in decision.reason


def test_the_outer_bound_alone_grants_nothing() -> None:
    """`scripts/` is inside allowed_paths, but no scope unblocks it, so it is closed."""
    decision = _evaluate(paths=("scripts/hunter_issue_agent_trigger.py",))

    assert decision.authorized is False
    assert "outside the governance scope authorized for Issue #402" in decision.reason


@pytest.mark.parametrize(
    "path",
    [
        ".githooks/pre-push",
        ".github/workflows/hunter-trusted-preflight-upgrade.yml",
        "scripts/hunter_pr_preflight.py",
        "scripts/hunter_connector_write_ingress.py",
        "scripts/hunter_governance_review_v2.py",
        "scripts/hunter_workflow_state.py",
        "docs/CODE_WRITE_POLICY.json",
        "pyproject.toml",
    ],
)
def test_root_of_trust_mutation_is_rejected_for_every_capability(path: str) -> None:
    """No capability on this ingress may write the authority that evaluates it."""
    for capability in ("feature-branch-write", "governance-maintenance"):
        decision = _evaluate(capability=capability, paths=ADR_PATHS + (path,))

        assert decision.authorized is False, f"{capability} was allowed to write {path}"
        assert path in decision.reason


def test_root_of_trust_is_rejected_even_when_it_is_the_only_change() -> None:
    decision = _evaluate(paths=("docs/CODE_WRITE_POLICY.json",))

    assert decision.authorized is False
    assert "root-of-trust path(s) no capability on this ingress may write" in decision.reason


def test_governance_maintenance_cannot_write_main() -> None:
    decision = _evaluate(target_ref="refs/heads/main")

    assert decision.authorized is False
    assert "direct write to protected branch" in decision.reason


def test_governance_maintenance_on_a_stale_base_is_rejected() -> None:
    """The base tip comes from trusted state, so a stale caller cannot certify itself."""
    decision = _evaluate(base_sha=STALE_BASE, tip=BASE_TIP)

    assert decision.authorized is False
    assert "stale base" in decision.reason


def test_governance_branch_must_bind_the_issue_it_claims() -> None:
    decision = _evaluate(target_ref="refs/heads/connector/issue-403-accept-adr-0036")

    assert decision.authorized is False
    assert "out of scope for Issue #402" in decision.reason


def test_unbound_capability_is_rejected_even_for_a_bound_writer() -> None:
    ordinary_only = _policy(writers=((CONNECTOR, "feature-branch-write"),))

    decision = _evaluate(policy=ordinary_only)

    assert decision.authorized is False
    assert "presented capability 'governance-maintenance'" in decision.reason


def test_capability_the_grant_does_not_define_is_rejected() -> None:
    decision = _evaluate(capability="root-of-trust-write")

    assert decision.authorized is False
    assert "presented capability 'root-of-trust-write'" in decision.reason


# --------------------------------------------------------------------------
# Policy parsing: the structural invariants that make the floor a floor
# --------------------------------------------------------------------------


def _document() -> dict:
    return json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))


def test_shipped_grant_authorizes_issue_402_adr_lifecycle_maintenance() -> None:
    """Acceptance: after merge, Issue #402 is executable through this ingress."""
    policy, error = ingress.load_policy()

    assert error == ""
    assert policy is not None
    assert "governance-maintenance" in policy.capabilities_for("fafa33")
    assert policy.scopes_authorized_for_issue("402") == frozenset({"adr-lifecycle"})

    decision = ingress.evaluate_write_request(
        _request(writer="fafa33"),
        policy,
        resolve_base_tip=_tip(),
    )

    assert decision.authorized is True, decision.reason


def test_shipped_grant_still_authorizes_the_ordinary_403_write_path() -> None:
    policy, _ = ingress.load_policy()
    assert policy is not None

    decision = ingress.evaluate_write_request(
        _request(
            writer="fafa33",
            capability="feature-branch-write",
            issue="403",
            target_ref=f"refs/heads/{ORDINARY_BRANCH}",
            paths=ORDINARY_PATHS,
        ),
        policy,
        resolve_base_tip=_tip(),
    )

    assert decision.authorized is True, decision.reason


def test_shipped_root_of_trust_covers_every_derived_authority() -> None:
    """Derived, not restated: the floor covers the real authority and its dependencies."""
    policy, _ = ingress.load_policy()
    assert policy is not None

    required, errors = prevention._authority_closure()
    assert errors == []
    for guarded in required:
        assert policy.is_root_of_trust(guarded), guarded


def test_the_derived_floor_reaches_the_push_boundary_and_its_dependencies() -> None:
    """The hook shells out; the scripts it reaches are authority just the same.

    `.githooks/pre-push` runs `hunter_pre_push`, which runs the canonical
    preflight, which runs the guards, which import the shared scope matcher. A
    floor derived only from the gate chain's own commands would stop at the
    guards and leave the push boundary's implementation writable.
    """
    required, errors = prevention._authority_closure()

    assert errors == []
    for reached in (
        "scripts/hunter_pre_push.py",
        "scripts/hunter_pr_preflight.py",
        "scripts/hunter_workflow_state.py",
        "scripts/hunter_github_transport.py",
        "scripts/hunter_connector_write_ingress.py",
        "scripts/hunter_governance_review_v2.py",
        "scripts/hunter_merge_readiness_v2.py",
        "scripts/hunter_candidate_admission.py",
    ):
        assert reached in required, reached


def test_the_derived_floor_does_not_over_block_ordinary_scripts() -> None:
    """A guard that swept in ordinary code would be a false-positive merge blocker.

    `acquire_sky_supply_basis` and `hunter_issue_agent_trigger` run from
    workflows that mint no merge-gating signal, so they are ordinary code that a
    future owner-authored scope may open -- not authority.
    """
    required, _ = prevention._authority_closure()

    assert "scripts/acquire_sky_supply_basis.py" not in required
    assert "scripts/hunter_issue_agent_trigger.py" not in required


def test_a_floor_that_drops_the_push_boundary_implementation_is_refused() -> None:
    """Regression for the review finding on PR #406.

    Dropping `hunter_pre_push` from the floor and unblocking it through an
    Issue-authorized scope previously passed both the guard and the loader, and
    `check_scope` then authorized writing the push boundary's implementation.
    """
    document = _document()
    grant = document["connector_write_ingress"]
    grant["root_of_trust_paths"] = [
        entry for entry in grant["root_of_trust_paths"] if entry != "scripts/hunter_pre_push.py"
    ]
    grant["governance_maintenance_scopes"]["push-boundary"] = {
        "purpose": "hostile",
        "unblocked_paths": ["scripts/hunter_pre_push.py"],
    }
    grant["governance_maintenance_authorizations"][0]["scopes"] = ["adr-lifecycle", "push-boundary"]

    errors = prevention.validate_connector_write_ingress(document)

    assert any("must cover scripts/hunter_pre_push.py" in error for error in errors)


def test_a_floor_derivation_that_cannot_read_its_sources_fails_closed(monkeypatch) -> None:
    """A floor derived from an unreadable source would be silently short."""
    monkeypatch.setattr(prevention, "PUSH_BOUNDARY_HOOK", ".githooks/does-not-exist")

    required, errors = prevention._authority_closure()

    assert any("is unreadable" in error for error in errors)
    assert any(
        "root-of-trust derivation failed" in error for error in prevention.validate_connector_write_ingress(_document())
    )
    assert required


def test_shipped_scopes_unblock_nothing_on_the_root_of_trust() -> None:
    policy, _ = ingress.load_policy()
    assert policy is not None

    for scope in policy.governance_scopes:
        for path in scope.unblocked_paths:
            assert not policy.is_root_of_trust(path), f"{scope.name} unblocks {path}"


def _forge(tmp_path: Path, document: dict) -> Path:
    forged = tmp_path / "CODE_WRITE_POLICY.json"
    forged.write_text(json.dumps(document), encoding="utf-8")
    return forged


def test_a_scope_that_unblocks_the_root_of_trust_makes_the_grant_unusable(tmp_path: Path) -> None:
    """`docs/` looks innocuous and would swallow the policy file itself.

    Entry-to-entry overlap runs in both directions on purpose: `docs/` is not
    itself a root-of-trust entry, but it covers one.
    """
    document = _document()
    document["connector_write_ingress"]["governance_maintenance_scopes"]["adr-lifecycle"]["unblocked_paths"] = ["docs/"]

    policy, error = ingress.load_policy(_forge(tmp_path, document))

    assert policy is None
    assert "may not unblock root-of-trust" in error


def test_a_scope_cannot_reach_outside_the_capability_allowed_paths(tmp_path: Path) -> None:
    document = _document()
    document["connector_write_ingress"]["governance_maintenance_scopes"]["adr-lifecycle"]["unblocked_paths"] = [
        "alembic/"
    ]

    policy, error = ingress.load_policy(_forge(tmp_path, document))

    assert policy is None
    assert "outside every capability's allowed paths" in error


def test_a_capability_that_does_not_close_the_root_of_trust_is_refused(tmp_path: Path) -> None:
    document = _document()
    capability = document["connector_write_ingress"]["additional_capabilities"]["governance-maintenance"]
    capability["prohibited_paths"] = [item for item in capability["prohibited_paths"] if item != "scripts/"]

    policy, error = ingress.load_policy(_forge(tmp_path, document))

    assert policy is None
    assert "must prohibit root-of-trust path(s)" in error


def test_an_authorization_for_an_unknown_scope_is_refused(tmp_path: Path) -> None:
    document = _document()
    document["connector_write_ingress"]["governance_maintenance_authorizations"][0]["scopes"] = ["root-of-trust"]

    policy, error = ingress.load_policy(_forge(tmp_path, document))

    assert policy is None
    assert "unknown scope(s): root-of-trust" in error


def test_an_additional_capability_may_not_redefine_the_required_capability(tmp_path: Path) -> None:
    document = _document()
    capabilities = document["connector_write_ingress"]["additional_capabilities"]
    capabilities["feature-branch-write"] = capabilities["governance-maintenance"]

    policy, error = ingress.load_policy(_forge(tmp_path, document))

    assert policy is None
    assert "redefines the required capability" in error


def test_equivalent_scope_spellings_stay_valid(tmp_path: Path) -> None:
    """A guard that accepted only one spelling of the same scope would be a defect."""
    document = _document()
    grant = document["connector_write_ingress"]
    grant["prohibited_paths"] = [
        entry.rstrip("/") + "/**" if entry.endswith("/") else entry for entry in grant["prohibited_paths"]
    ]
    grant["additional_capabilities"]["governance-maintenance"]["prohibited_paths"] = grant["prohibited_paths"]

    policy, error = ingress.load_policy(_forge(tmp_path, document))

    assert error == ""
    assert policy is not None
    assert prevention.validate_connector_write_ingress(document) == []


def test_an_unblocked_directory_does_not_leak_to_a_sibling_with_the_same_prefix() -> None:
    """`docs/ADR/` unblocks the ADR directory, not a sibling whose name starts with it."""
    narrow = _policy(
        prohibited_paths=CLOSED_BY_DEFAULT + ("docs/ADRIFT/",),
        additional_capabilities=(
            replace(
                _policy().additional_capabilities[0],
                prohibited_paths=CLOSED_BY_DEFAULT + ("docs/ADRIFT/",),
            ),
        ),
    )

    decision = _evaluate(policy=narrow, paths=("docs/ADRIFT/notes.md",))

    assert decision.authorized is False
    assert "outside the governance scope" in decision.reason


def test_the_shipped_grant_passes_the_deterministic_guard() -> None:
    assert prevention.validate_connector_write_ingress(_document()) == []


def test_a_directory_spelled_floor_still_covers_the_gate_chain() -> None:
    """The floor is a scope statement: `scripts/` covers every script beneath it."""
    document = _document()
    grant = document["connector_write_ingress"]
    grant["root_of_trust_paths"] = [
        entry for entry in grant["root_of_trust_paths"] if not entry.startswith("scripts/")
    ] + ["scripts/"]

    assert prevention.validate_connector_write_ingress(document) == []


def test_the_guard_rejects_a_floor_that_falls_behind_the_authority_it_protects() -> None:
    document = _document()
    grant = document["connector_write_ingress"]
    grant["root_of_trust_paths"] = [
        entry for entry in grant["root_of_trust_paths"] if entry != "scripts/hunter_pr_preflight.py"
    ]

    errors = prevention.validate_connector_write_ingress(document)

    assert any("must cover scripts/hunter_pr_preflight.py" in error for error in errors)


def test_the_guard_rejects_governance_maintenance_without_issue_authorization() -> None:
    document = _document()
    capability = document["connector_write_ingress"]["additional_capabilities"]["governance-maintenance"]
    capability["requires_issue_authorization"] = False

    errors = prevention.validate_connector_write_ingress(document)

    assert any("must require explicit per-Issue authorization" in error for error in errors)


# --------------------------------------------------------------------------
# Same-candidate self-escalation: propose a wider grant, never rely on it
# --------------------------------------------------------------------------


def test_widening_is_detected_and_narrowing_is_not() -> None:
    trusted = _policy()

    assert ingress.grant_widening(trusted, trusted) == ()
    assert ingress.grant_widening(trusted, _policy(prohibited_paths=CLOSED_BY_DEFAULT + ("docs/VISION.md",))) == ()

    widened = _policy(
        issue_authorizations=trusted.issue_authorizations
        + (ingress.IssueAuthorization(issue="777", scopes=frozenset({"adr-lifecycle"})),)
    )
    assert any("Issue #777" in reason for reason in ingress.grant_widening(trusted, widened))

    reopened = _policy(prohibited_paths=tuple(p for p in CLOSED_BY_DEFAULT if p != "scripts/"))
    assert any(
        "drops 'feature-branch-write' prohibitions" in reason for reason in ingress.grant_widening(trusted, reopened)
    )

    unfloored = _policy(root_of_trust_paths=tuple(p for p in ROOT_OF_TRUST if p != "docs/CODE_WRITE_POLICY.json"))
    assert any("drops root-of-trust protection" in reason for reason in ingress.grant_widening(trusted, unfloored))

    new_writer = _policy(writers=_policy().writers + (("someone-else", "governance-maintenance"),))
    assert any("adds writer grant" in reason for reason in ingress.grant_widening(trusted, new_writer))


def test_widening_is_compared_semantically_not_by_literal_text() -> None:
    """Re-spelling a scope entry is not widening; genuinely narrowing one is.

    A detector that compared entry text would block a candidate that changed
    nothing, which is itself a defect -- and would still have to catch the case
    where a broad prohibition is replaced by a narrower one.
    """
    trusted = _policy()

    respelled = _policy(
        prohibited_paths=tuple(
            entry.rstrip("/") + "/**" if entry.endswith("/") else entry for entry in CLOSED_BY_DEFAULT
        ),
        root_of_trust_paths=tuple(
            entry.rstrip("/") + "/**" if entry.endswith("/") else entry for entry in ROOT_OF_TRUST
        ),
    )
    assert ingress.grant_widening(trusted, respelled) == ()

    narrowed_prohibition = _policy(
        prohibited_paths=tuple(".github/workflows/" if entry == ".github/" else entry for entry in CLOSED_BY_DEFAULT)
    )
    reasons = ingress.grant_widening(trusted, narrowed_prohibition)
    assert any("drops 'feature-branch-write' prohibitions on .github/" in reason for reason in reasons)


def test_an_equivalently_spelled_head_policy_is_not_treated_as_escalation(monkeypatch) -> None:
    """The admission consequence of the same property, end to end."""
    trusted = _policy()
    respelled = _policy(
        root_of_trust_paths=tuple(
            entry.rstrip("/") + "/**" if entry.endswith("/") else entry for entry in ROOT_OF_TRUST
        )
    )

    state, description = _admission(
        monkeypatch, policy=trusted, head_policy=respelled, receipt=_authorization(grant=trusted).document()
    )

    assert state == "success", description


# --------------------------------------------------------------------------
# Admission: every claim re-derived from trusted evidence
# --------------------------------------------------------------------------


SUCCESSFUL_BRANCH_RUN = {
    "head_sha": HEAD,
    "head_branch": GOVERNANCE_BRANCH,
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


TRUSTED_RUN_ID = 209
HOSTED_PROOF = [
    {
        "id": 9,
        "context": core._upgrade_status_context(601),
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
    "pull_requests": [{"number": 601, "head": {"sha": HEAD}}],
}


def _commit(sha: str = HEAD, signer: str = CONNECTOR) -> dict:
    return {
        "sha": sha,
        "committer": {"login": signer},
        "commit": {"verification": {"verified": True, "reason": "valid"}},
    }


def _authorization(
    *, grant: ingress.ConnectorIngressPolicy | None = None, **overrides: object
) -> ingress.ConnectorWriteAuthorization:
    base = grant if grant is not None else _policy()
    authorization = ingress.ConnectorWriteAuthorization(
        writer=CONNECTOR,
        capability="governance-maintenance",
        issue=GOVERNANCE_ISSUE,
        base_ref="main",
        base_sha=BASE_TIP,
        target_ref=GOVERNANCE_BRANCH,
        paths=ADR_PATHS,
        changes=_changes_for(ADR_PATHS),
        governance_scopes=("adr-lifecycle",),
        grant_fingerprint=base.fingerprint,
    )
    authorization = replace(authorization, **overrides)  # type: ignore[arg-type]
    if "changes" not in overrides:
        authorization = replace(authorization, changes=_changes_for(authorization.paths))
    return authorization


def _admission(
    monkeypatch: pytest.MonkeyPatch,
    *,
    receipt: object | None = "default",
    head_ref: str = GOVERNANCE_BRANCH,
    changed_paths: tuple[str, ...] = ADR_PATHS,
    merge_base: str = BASE_TIP,
    statuses: list[dict] | None = None,
    policy: ingress.ConnectorIngressPolicy | None = None,
    head_policy: ingress.ConnectorIngressPolicy | None = None,
) -> tuple[str, str]:
    """Drive candidate_admission over stubbed trusted evidence.

    `policy` is the trusted default-branch grant the controller evaluates under.
    `head_policy` is the grant the candidate carries at its own head; it defaults
    to the trusted one and differs only when a test drives self-escalation.
    """

    grant = policy if policy is not None else _policy()
    head_grant = head_policy if head_policy is not None else grant
    monkeypatch.setattr(ingress, "load_policy", lambda *_a, **_k: (grant, ""))
    monkeypatch.setattr(ingress, "parse_policy", lambda *_a, **_k: (head_grant, ""))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_args: (
            True,
            tuple(core.PullRequestFile("modified", path, blob_sha=_digest_for(path)) for path in changed_paths),
            None,
        ),
    )
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, changed_paths, None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))
    monkeypatch.setattr(core, "load_ingress_provenance_policy", lambda: (frozenset({CONNECTOR}), "", None))
    monkeypatch.setattr(core, "load_connector_write_ingress_policy", lambda: (True, frozenset({CONNECTOR}), None))

    document = _authorization().document() if receipt == "default" else receipt

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "pulls/601/commits" in path:
            return [_commit()]
        if path.startswith("pulls/601"):
            return {"head": {"ref": head_ref, "sha": HEAD}, "base": {"ref": "main"}}
        if path.startswith(f"contents/{core.CODE_WRITE_POLICY_RELATIVE_PATH}"):
            # `parse_policy` is stubbed above, so the bytes only have to decode.
            return {"content": base64.b64encode(b"{}").decode("ascii")}
        if path.startswith("contents/.hunter/"):
            if document is None:
                raise transport.GitHubRequestError("Not Found", category="permanent", status_code=404)
            encoded = base64.b64encode(json.dumps(document).encode("utf-8")).decode("ascii")
            return {"content": encoded}
        if path.startswith("compare/"):
            return {"merge_base_commit": {"sha": merge_base}}
        if "statuses" in path:
            return statuses if statuses is not None else HOSTED_PROOF
        if path == f"actions/runs/{TRUSTED_RUN_ID}":
            return TRUSTED_UPGRADE_RUN
        if "actions/runs" in path:
            return {
                "workflow_runs": [
                    {
                        **SUCCESSFUL_BRANCH_RUN,
                        "head_sha": _requested_head_sha(path),
                        "head_branch": head_ref,
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)
    # The Issue #412 pre-ready hostile review gate is an independent admission
    # prerequisite with its own regression suite
    # (tests/test_issue_412_prevention_gate.py). Stubbing it keeps this harness
    # on the ingress contract it was written for.
    monkeypatch.setattr(core, "verify_pre_ready_hostile_review", lambda *_args: ("success", "reviewed"))
    return core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 601)


def test_owner_authorized_adr_lifecycle_candidate_is_admitted(monkeypatch) -> None:
    state, description = _admission(monkeypatch)

    assert state == "success", description


def test_same_pr_capability_widening_and_self_use_fails(monkeypatch) -> None:
    """The candidate authorizes a new Issue at its own head and writes under it."""
    trusted = _policy(issue_authorizations=())
    widened = _policy()

    state, description = _admission(monkeypatch, policy=trusted, head_policy=widened)

    assert state == "failure"
    assert "may not widen the grant it is authorized under" in description
    assert "authorizes governance maintenance for Issue #402" in description


def test_same_pr_widening_fails_even_with_a_receipt_minted_under_the_trusted_grant(monkeypatch) -> None:
    """A perfectly valid receipt does not rescue a head that widens the grant.

    Here the candidate does ordinary in-scope work under the trusted grant and
    *separately* widens the grant at its own head. The receipt pins the trusted
    grant, so the version pin is satisfied; the widening is refused anyway.
    """
    trusted = _policy()
    widened = _policy(
        issue_authorizations=trusted.issue_authorizations
        + (ingress.IssueAuthorization(issue="390", scopes=frozenset({"adr-lifecycle"})),)
    )

    state, description = _admission(
        monkeypatch,
        policy=trusted,
        head_policy=widened,
        receipt=_authorization(grant=trusted).document(),
    )

    assert state == "failure"
    assert "may not widen the grant it is authorized under" in description
    assert "authorizes governance maintenance for Issue #390" in description


def test_head_that_reopens_the_root_of_trust_fails(monkeypatch) -> None:
    trusted = _policy()
    unfloored = _policy(root_of_trust_paths=tuple(p for p in ROOT_OF_TRUST if p != "docs/CODE_WRITE_POLICY.json"))

    state, description = _admission(monkeypatch, policy=trusted, head_policy=unfloored)

    assert state == "failure"
    assert "drops root-of-trust protection for docs/CODE_WRITE_POLICY.json" in description


def test_unauthorized_root_of_trust_mutation_fails_admission(monkeypatch) -> None:
    paths = ADR_PATHS + ("scripts/hunter_governance_review_v2.py",)
    state, description = _admission(
        monkeypatch,
        changed_paths=paths,
        receipt=_authorization(paths=paths).document(),
    )

    assert state == "failure"
    assert "root-of-trust path(s) no capability on this ingress may write" in description


def test_candidate_for_an_unauthorized_issue_fails_admission(monkeypatch) -> None:
    """Wrong Issue, re-derived from the branch rather than believed from the receipt."""
    state, description = _admission(
        monkeypatch,
        head_ref="connector/issue-390-composition-root",
        receipt=_authorization(issue="390", target_ref="connector/issue-390-composition-root").document(),
    )

    assert state == "failure"
    assert "carries no owner-authored governance-maintenance authorization" in description


def test_receipt_claiming_a_scope_the_issue_does_not_hold_fails(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        receipt=_authorization(governance_scopes=("adr-lifecycle", "defect-registry")).document(),
    )

    assert state == "failure"
    assert "is authorized for adr-lifecycle" in description


def test_unauthorized_governance_path_fails_admission(monkeypatch) -> None:
    """Issue #402 holds adr-lifecycle only; a defect-registry path is out of scope."""
    paths = ADR_PATHS + ("docs/DEFECT_REGISTRY.json",)
    state, description = _admission(
        monkeypatch,
        changed_paths=paths,
        receipt=_authorization(paths=paths).document(),
    )

    assert state == "failure"
    assert "outside the governance scope authorized for Issue #402" in description


def test_stale_base_fails_admission(monkeypatch) -> None:
    """The receipt's base must be this candidate's trusted fork point."""
    state, description = _admission(monkeypatch, merge_base=STALE_BASE)

    assert state == "failure"
    assert "is not this candidate's fork point" in description


def test_receipt_minted_under_an_older_grant_is_stale(monkeypatch) -> None:
    """Replay: a receipt from a previous grant version admits nothing."""
    older = _policy(issue_authorizations=())

    state, description = _admission(monkeypatch, receipt=_authorization(grant=older).document())

    assert state == "failure"
    assert "re-authorize against the current grant" in description


def test_receipt_replayed_onto_another_branch_fails(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        head_ref="connector/issue-402-other-work",
        receipt=_authorization().document(),
    )

    assert state == "failure"
    assert "is bound to branch" in description


def test_missing_hosted_exact_head_proof_leaves_the_candidate_unadmitted(monkeypatch) -> None:
    state, description = _admission(monkeypatch, statuses=[])

    assert state == "failure"
    assert "trusted hosted exact-head canonical preflight proof" in description


def test_hosted_proof_bound_to_another_pr_does_not_admit(monkeypatch) -> None:
    other = [{"id": 9, "context": core._upgrade_status_context(999), "state": "success"}]

    state, description = _admission(monkeypatch, statuses=other)

    assert state == "failure"
    assert "trusted hosted exact-head canonical preflight proof" in description


def test_governance_candidate_without_a_receipt_fails(monkeypatch) -> None:
    state, description = _admission(monkeypatch, receipt=None)

    assert state == "failure"
    assert "carries no exact-head authorization receipt" in description


def test_head_policy_that_cannot_be_read_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(core, "read_head_code_write_policy", lambda *_a: ("unavailable", None, "boom"))

    state, description = _admission(monkeypatch)

    assert state == "failure"
    assert "head code-write policy evidence is unavailable" in description


def test_head_without_a_code_write_policy_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(core, "read_head_code_write_policy", lambda *_a: ("absent", None, None))

    state, description = _admission(monkeypatch)

    assert state == "failure"
    assert "carries no canonical code-write policy" in description
