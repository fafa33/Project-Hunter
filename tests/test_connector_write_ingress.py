"""Deterministic hostile tests for the Issue #403 governed connector write ingress.

Three boundaries are covered:

* `hunter_connector_write_ingress` -- the write-time authorization decision
  (writer identity/capability, direct-main attempt, connector namespace, Issue
  scope, trusted base provenance and staleness, path scope);
* the commit-bound authorization receipt -- a signed commit from an allowlisted
  login proves who wrote it, never that the write crossed the governed
  authorizer, so admission re-derives every claim from trusted evidence;
* `hunter_governance_review_v2` -- the admission-time consequence, namely that a
  connector-origin head stays unadmitted until both the trusted re-derivation and
  the trusted hosted exact-head canonical preflight prove it, and that connector
  identity can never be counted as clone-capable pre-push proof.
"""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path

import hunter_connector_write_ingress as ingress
import hunter_governance_review_v2 as core
import hunter_workflow_state as workflow_state
import pytest

ROOT = Path(__file__).resolve().parents[1]

BASE_TIP = "a" * 40
STALE_BASE = "b" * 40
HEAD = "c" * 40
ADVANCED_TIP = "e" * 40

CONNECTOR = "trusted-connector-app[bot]"
CONNECTOR_BRANCH = "connector/issue-403-example"
CONTENT_PATHS = ("src/hunter/example.py", "tests/test_example.py")


def _tip(sha: str = BASE_TIP):
    """A stand-in for trusted repository state, never for caller input."""

    def resolver(_base_ref: str) -> str:
        return sha

    return resolver


def _policy(**overrides: object) -> ingress.ConnectorIngressPolicy:
    policy = ingress.ConnectorIngressPolicy(
        enabled=True,
        writers=((CONNECTOR, "feature-branch-write"),),
        required_capability="feature-branch-write",
        base_ref="main",
        forbidden_target_refs=frozenset({"main", "HEAD"}),
        branch_namespace="connector/",
        branch_pattern_template="connector/issue-{issue}-*",
        allowed_paths=("src/", "tests/", "docs/"),
        prohibited_paths=(".githooks/", ".github/", "scripts/", "docs/CODE_WRITE_POLICY.json"),
        require_exact_base_tip=True,
        local_pre_push_equivalent=False,
    )
    return replace(policy, **overrides)  # type: ignore[arg-type]


def _request(**overrides: object) -> ingress.ConnectorWriteRequest:
    request = ingress.ConnectorWriteRequest(
        writer=CONNECTOR,
        capability="feature-branch-write",
        issue="403",
        target_ref=f"refs/heads/{CONNECTOR_BRANCH}",
        base_ref="main",
        base_sha=BASE_TIP,
        paths=CONTENT_PATHS,
    )
    return replace(request, **overrides)  # type: ignore[arg-type]


def _evaluate(*, tip: str = BASE_TIP, **overrides: object) -> ingress.IngressDecision:
    return ingress.evaluate_write_request(_request(**overrides), _policy(), resolve_base_tip=_tip(tip))


# --------------------------------------------------------------------------
# Baseline: the governed path must actually authorize a compliant request, so
# every rejection below is a real boundary rather than a gate that refuses all.
# --------------------------------------------------------------------------


def test_compliant_feature_branch_write_is_authorized() -> None:
    decision = _evaluate()

    assert decision.authorized is True
    assert decision.authorization is not None
    assert "Draft/unadmitted" in decision.reason


# --------------------------------------------------------------------------
# Hostile: arbitrary writer rejection
# --------------------------------------------------------------------------


def test_arbitrary_api_writer_is_rejected() -> None:
    decision = _evaluate(writer="drive-by-account")

    assert decision.authorized is False
    assert "unauthorized connector writer" in decision.reason


def test_authorized_writer_without_the_granted_capability_is_rejected() -> None:
    decision = _evaluate(capability="admin-write")

    assert decision.authorized is False
    assert "capability" in decision.reason


def test_writer_identity_is_not_satisfied_by_a_login_prefix() -> None:
    decision = _evaluate(writer=CONNECTOR[:-5])

    assert decision.authorized is False
    assert "unauthorized connector writer" in decision.reason


def test_disabled_grant_authorizes_nothing() -> None:
    decision = ingress.evaluate_write_request(_request(), _policy(enabled=False), resolve_base_tip=_tip())

    assert decision.authorized is False
    assert "not enabled" in decision.reason


def test_enabled_grant_without_a_bound_writer_authorizes_nothing() -> None:
    decision = ingress.evaluate_write_request(_request(), _policy(writers=()), resolve_base_tip=_tip())

    assert decision.authorized is False
    assert "binds no writer identity" in decision.reason


def test_missing_policy_fails_closed() -> None:
    decision = ingress.evaluate_write_request(
        _request(), None, resolve_base_tip=_tip(), policy_error="policy is missing"
    )

    assert decision.authorized is False
    assert "policy is missing" in decision.reason


# --------------------------------------------------------------------------
# Hostile: direct main attempt and connector namespace
# --------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["main", "refs/heads/main", " main ", "HEAD"])
def test_direct_main_write_attempt_is_rejected(target: str) -> None:
    decision = _evaluate(target_ref=target)

    assert decision.authorized is False
    assert "protected branch" in decision.reason


def test_non_branch_write_target_is_rejected() -> None:
    decision = _evaluate(target_ref="refs/tags/v1.0.0")

    assert decision.authorized is False
    assert "is not a branch ref" in decision.reason


def test_write_outside_the_connector_namespace_is_rejected() -> None:
    decision = _evaluate(target_ref="claude/issue-403-example")

    assert decision.authorized is False
    assert "outside the connector namespace" in decision.reason


def test_base_branch_other_than_the_authorized_base_is_rejected() -> None:
    decision = _evaluate(base_ref="release/3.6")

    assert decision.authorized is False
    assert "is not the authorized base" in decision.reason


# --------------------------------------------------------------------------
# Hostile: Issue/task scope mismatch
# --------------------------------------------------------------------------


def test_branch_bound_to_a_different_issue_is_rejected() -> None:
    decision = _evaluate(issue="404")

    assert decision.authorized is False
    assert "out of scope for Issue #404" in decision.reason


def test_branch_without_issue_binding_is_rejected() -> None:
    decision = _evaluate(target_ref="connector/adhoc-work")

    assert decision.authorized is False
    assert "out of scope for Issue #403" in decision.reason


@pytest.mark.parametrize("issue", ["", "abc", "0", "403x", "40 3"])
def test_missing_or_malformed_governing_issue_is_rejected(issue: str) -> None:
    decision = _evaluate(issue=issue)

    assert decision.authorized is False
    assert "single governing Issue number" in decision.reason


def test_issue_number_is_not_satisfied_by_a_substring_match() -> None:
    """Issue #40 must not be accepted by a branch that binds Issue #403."""
    decision = _evaluate(issue="40")

    assert decision.authorized is False
    assert "out of scope for Issue #40" in decision.reason


def test_branch_pattern_does_not_admit_a_subpath_of_the_authorized_branch() -> None:
    """Branch matching is pattern matching, not path-scope containment."""
    policy = _policy(branch_pattern_template="connector/issue-{issue}")

    assert (
        ingress.evaluate_write_request(
            _request(target_ref="connector/issue-403"), policy, resolve_base_tip=_tip()
        ).authorized
        is True
    )
    assert (
        ingress.evaluate_write_request(
            _request(target_ref="connector/issue-403/x"), policy, resolve_base_tip=_tip()
        ).authorized
        is False
    )


# --------------------------------------------------------------------------
# Hostile: base provenance comes from trusted repository state (Codex P1)
# --------------------------------------------------------------------------


def test_stale_base_commit_is_rejected() -> None:
    decision = _evaluate(base_sha=STALE_BASE)

    assert decision.authorized is False
    assert decision.reason.startswith("stale base:")


def test_caller_cannot_certify_its_own_stale_base() -> None:
    """A stale caller submitting a self-consistent stale pair must still fail.

    The request carries no base-tip field at all, so the only way to answer "is
    this the current tip" is to ask trusted repository state.
    """
    with pytest.raises(ValueError, match="unknown field"):
        ingress.ConnectorWriteRequest.from_dict(
            {
                "writer": CONNECTOR,
                "capability": "feature-branch-write",
                "issue": "403",
                "target_ref": CONNECTOR_BRANCH,
                "base_ref": "main",
                "base_sha": STALE_BASE,
                "observed_base_tip_sha": STALE_BASE,
                "paths": list(CONTENT_PATHS),
            }
        )

    # And the same stale commit, evaluated against the real current tip, loses.
    decision = ingress.evaluate_write_request(_request(base_sha=STALE_BASE), _policy(), resolve_base_tip=_tip(BASE_TIP))
    assert decision.authorized is False
    assert decision.reason.startswith("stale base:")


def test_absent_base_provenance_fails_closed() -> None:
    decision = _evaluate(base_sha="")

    assert decision.authorized is False
    assert "full base commit SHA" in decision.reason


def test_abbreviated_base_sha_is_not_accepted_as_exact_provenance() -> None:
    decision = _evaluate(base_sha=BASE_TIP[:10], tip=BASE_TIP[:10])

    assert decision.authorized is False
    assert "full base commit SHA" in decision.reason


def test_unusable_trusted_base_tip_fails_closed() -> None:
    decision = ingress.evaluate_write_request(_request(), _policy(), resolve_base_tip=_tip("not-a-sha"))

    assert decision.authorized is False
    assert "no usable base tip" in decision.reason


def test_unavailable_trusted_base_tip_fails_closed() -> None:
    def broken(_base_ref: str) -> str:
        raise ingress.BaseTipUnavailable("git ls-remote failed")

    decision = ingress.evaluate_write_request(_request(), _policy(), resolve_base_tip=broken)

    assert decision.authorized is False
    assert "trusted base tip is unavailable" in decision.reason


def test_authorization_without_a_resolver_fails_closed() -> None:
    decision = ingress.evaluate_write_request(_request(), _policy(), resolve_base_tip=None)

    assert decision.authorized is False
    assert "cannot resolve the trusted base tip" in decision.reason


def test_base_advancing_before_application_invalidates_the_authorization() -> None:
    """Closes the TOCTOU window between authorization and the write landing."""
    decision = _evaluate()
    assert decision.authorized is True and decision.authorization is not None

    assert ingress.confirm_base_unchanged(decision.authorization, _tip(BASE_TIP)) == ""

    blocked = ingress.confirm_base_unchanged(decision.authorization, _tip(ADVANCED_TIP))
    assert "advanced from" in blocked
    assert "re-authorize against the current tip" in blocked


def test_confirm_base_unchanged_fails_closed_when_state_is_unavailable() -> None:
    decision = _evaluate()
    assert decision.authorization is not None

    def broken(_base_ref: str) -> str:
        raise ingress.BaseTipUnavailable("network down")

    assert "trusted base tip is unavailable" in ingress.confirm_base_unchanged(decision.authorization, broken)


# --------------------------------------------------------------------------
# Hostile: unauthorized paths
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".githooks/pre-push",
        ".github/workflows/ci.yml",
        "scripts/hunter_pr_preflight.py",
        "docs/CODE_WRITE_POLICY.json",
    ],
)
def test_connector_cannot_write_the_guards_that_bind_it(path: str) -> None:
    decision = _evaluate(paths=("src/hunter/example.py", path))

    assert decision.authorized is False
    assert "prohibited path(s)" in decision.reason


def test_path_outside_the_granted_scope_is_rejected() -> None:
    decision = _evaluate(paths=("pyproject.toml",))

    assert decision.authorized is False
    assert "outside the granted connector scope" in decision.reason


def test_traversal_path_is_rejected() -> None:
    decision = _evaluate(paths=("src/../.githooks/pre-push",))

    assert decision.authorized is False
    assert "repository-relative" in decision.reason


def test_write_without_declared_paths_fails_closed() -> None:
    decision = _evaluate(paths=())

    assert decision.authorized is False
    assert "no changed paths" in decision.reason


def test_request_cannot_declare_the_receipt_or_other_governed_ingress_state() -> None:
    assert "receipt as changed content" in _evaluate(paths=(ingress.AUTHORIZATION_RECEIPT_PATH,)).reason
    assert "governed .hunter/ ingress state" in _evaluate(paths=(".hunter/other.json",)).reason


# --------------------------------------------------------------------------
# Request parsing must not silently ignore claims it cannot read
# --------------------------------------------------------------------------


def test_unknown_request_field_is_refused_rather_than_ignored() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        ingress.ConnectorWriteRequest.from_dict({"writer": CONNECTOR, "auto_merge": True})


def test_string_paths_value_is_refused_rather_than_iterated_per_character() -> None:
    with pytest.raises(ValueError, match="must be an array of paths"):
        ingress.ConnectorWriteRequest.from_dict({"writer": CONNECTOR, "paths": "src/hunter/example.py"})


# --------------------------------------------------------------------------
# The authorization receipt is canonical and self-consistent
# --------------------------------------------------------------------------


def test_authorization_id_is_deterministic_over_exactly_the_claims() -> None:
    first = _evaluate().authorization
    second = _evaluate().authorization
    assert first is not None and second is not None
    assert first.authorization_id == second.authorization_id

    widened = replace(first, paths=first.paths + ("pyproject.toml",))
    assert widened.authorization_id != first.authorization_id


def test_receipt_round_trips_and_rejects_a_tampered_claim() -> None:
    authorization = _evaluate().authorization
    assert authorization is not None

    parsed, error = ingress.ConnectorWriteAuthorization.from_document(authorization.document())
    assert error == "" and parsed == authorization

    tampered = authorization.document()
    tampered["claims"]["paths"] = ["pyproject.toml"]
    broken, reason = ingress.ConnectorWriteAuthorization.from_document(tampered)
    assert broken is None
    assert "does not match its own claims" in reason


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda doc: doc.__setitem__("schema", "other"), "schema must be"),
        (lambda doc: doc.__setitem__("claims", "nope"), "claims must be an object"),
        (lambda doc: doc["claims"].pop("writer"), "exactly the canonical claim set"),
        (lambda doc: doc["claims"].__setitem__("extra", 1), "exactly the canonical claim set"),
        (lambda doc: doc["claims"].__setitem__("paths", "src/x.py"), "array of path strings"),
        (lambda doc: doc.__setitem__("authorization_id", ""), "does not match its own claims"),
    ],
)
def test_malformed_receipt_documents_fail_closed(mutate, expected: str) -> None:
    authorization = _evaluate().authorization
    assert authorization is not None
    document = authorization.document()
    mutate(document)

    parsed, reason = ingress.ConnectorWriteAuthorization.from_document(document)

    assert parsed is None
    assert expected in reason


# --------------------------------------------------------------------------
# The shipped grant must be usable as shipped, with its own guards prohibited.
# --------------------------------------------------------------------------


def test_shipped_policy_loads_with_the_boundaries_that_bound_it() -> None:
    policy, error = ingress.load_policy()

    assert error == ""
    assert policy is not None
    assert policy.local_pre_push_equivalent is False
    assert policy.require_exact_base_tip is True
    assert policy.base_ref == "main"
    assert "main" in policy.forbidden_target_refs
    assert policy.branch_pattern_template.startswith(policy.branch_namespace)


def test_shipped_grant_is_active_and_bound_so_no_second_bootstrap_is_needed() -> None:
    """Post-merge activation must be complete: no follow-up policy edit required.

    The grant shipping inert would mean the connector still could not perform a
    compliant write after merge, and the owner would have to bootstrap another
    clone-capable code-write PR just to switch it on -- which is exactly the
    manual relay Issue #403 exists to remove.
    """
    policy, error = ingress.load_policy()

    assert error == ""
    assert policy is not None
    assert policy.enabled is True
    assert policy.writers
    assert all(login and capability in policy.granted_capabilities() for login, capability in policy.writers)
    assert policy.required_capability in {capability for _login, capability in policy.writers}


def test_shipped_grant_authorizes_a_compliant_connector_write_as_shipped() -> None:
    """End-to-end on the real on-disk grant, with only the base tip injected."""
    policy, _ = ingress.load_policy()
    assert policy is not None

    bound_login = policy.writers[0][0]
    decision = ingress.evaluate_write_request(
        _request(writer=bound_login, target_ref=f"refs/heads/{CONNECTOR_BRANCH}"),
        policy,
        resolve_base_tip=_tip(),
    )

    assert decision.authorized is True, decision.reason


def test_shipped_policy_prohibits_writing_its_own_guards() -> None:
    policy, _ = ingress.load_policy()
    assert policy is not None

    for guarded in (".githooks/pre-push", ".github/workflows/ci.yml", "scripts/hunter_pr_preflight.py"):
        assert any(ingress.path_matches_scope_entry(guarded, entry) for entry in policy.prohibited_paths)


def test_ingress_shares_one_scope_matching_semantic_with_the_workflow_state_gate() -> None:
    """Two implementations of "is this path in scope" would be two answers."""
    assert ingress.path_matches_scope_entry is workflow_state.path_matches_scope_entry


def test_unreadable_policy_fails_closed(tmp_path: Path) -> None:
    broken = tmp_path / "CODE_WRITE_POLICY.json"
    broken.write_text("{not json", encoding="utf-8")

    policy, error = ingress.load_policy(broken)

    assert policy is None
    assert "unreadable" in error


def _forge(tmp_path: Path, document: dict) -> Path:
    forged = tmp_path / "CODE_WRITE_POLICY.json"
    forged.write_text(json.dumps(document), encoding="utf-8")
    return forged


def _policy_document() -> dict:
    return json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("local_pre_push_equivalent", True, "local pre-push equivalence"),
        ("require_exact_base_tip", False, "exact base tip"),
        ("branch_pattern_template", "connector/anything-*", "governing Issue"),
        ("branch_namespace", "connector", "namespace ending in '/'"),
        ("branch_pattern_template", "claude/issue-{issue}-*", "inside the connector namespace"),
    ],
)
def test_policy_that_loosens_its_own_boundary_is_refused(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    document = _policy_document()
    document["connector_write_ingress"][field] = value

    policy, error = ingress.load_policy(_forge(tmp_path, document))

    assert policy is None
    assert expected in error


@pytest.mark.parametrize("login", ["not a login", "-leading-dash", "trailing-dash-", "a" * 60])
def test_malformed_bound_writer_login_is_refused(tmp_path: Path, login: str) -> None:
    document = _policy_document()
    document["connector_write_ingress"]["authorized_writers"][0]["login"] = login

    policy, error = ingress.load_policy(_forge(tmp_path, document))

    assert policy is None
    assert "malformed" in error


def test_duplicate_writer_grant_is_refused(tmp_path: Path) -> None:
    document = _policy_document()
    writers = document["connector_write_ingress"]["authorized_writers"]
    writers.append(dict(writers[0]))

    policy, error = ingress.load_policy(_forge(tmp_path, document))

    assert policy is None
    assert "duplicate writer" in error


# --------------------------------------------------------------------------
# Admission: trusted re-derivation of the exact authorization
# --------------------------------------------------------------------------


SUCCESSFUL_BRANCH_RUN = {
    "head_sha": HEAD,
    "name": core.PRE_PR_WORKFLOW_NAME,
    "path": core.PRE_PR_WORKFLOW_PATH,
    "event": "push",
    "status": "completed",
    "conclusion": "success",
    "id": 100,
}
HOSTED_PROOF = [{"id": 9, "context": core._upgrade_status_context(501), "state": "success"}]


def _commit(sha: str = HEAD, signer: str = CONNECTOR) -> dict:
    return {
        "sha": sha,
        "committer": {"login": signer},
        "commit": {"verification": {"verified": True, "reason": "valid"}},
    }


def _authorization(
    *, grant: ingress.ConnectorIngressPolicy | None = None, **overrides: object
) -> ingress.ConnectorWriteAuthorization:
    """A receipt minted under `grant`, defaulting to the harness grant.

    The receipt pins the exact grant version it was authorized under (Issue #405),
    so a receipt must be minted under the same grant the admission harness is
    driving; a receipt from any other version is stale by construction.
    """

    authorization = ingress.ConnectorWriteAuthorization(
        writer=CONNECTOR,
        capability="feature-branch-write",
        issue="403",
        base_ref="main",
        base_sha=BASE_TIP,
        target_ref=CONNECTOR_BRANCH,
        paths=CONTENT_PATHS,
        governance_scopes=(),
        grant_fingerprint=(grant if grant is not None else _policy()).fingerprint,
    )
    return replace(authorization, **overrides)  # type: ignore[arg-type]


def _admission(
    monkeypatch: pytest.MonkeyPatch,
    *,
    commits: list[dict] | None = None,
    receipt: object | None = "default",
    head_ref: str = CONNECTOR_BRANCH,
    changed_paths: tuple[str, ...] = CONTENT_PATHS,
    merge_base: str = BASE_TIP,
    statuses: list[dict] | None = None,
    branch_runs: list[dict] | None = None,
    connector_policy: tuple[bool, frozenset[str], str | None] = (True, frozenset({CONNECTOR}), None),
    signers: frozenset[str] = frozenset({"claude", "fafa33"}),
    policy: ingress.ConnectorIngressPolicy | None = None,
    head_policy: ingress.ConnectorIngressPolicy | None = None,
    head_policy_error: str = "",
) -> tuple[str, str]:
    """Drive candidate_admission over stubbed trusted evidence.

    `policy` is the trusted default-branch grant the controller evaluates under;
    `head_policy` is the grant the candidate carries at its own head, which
    defaults to the same one. They differ only when a test is driving the Issue
    #405 same-candidate self-escalation boundary.
    """

    grant = policy if policy is not None else _policy()
    head_grant = head_policy if head_policy is not None else grant
    monkeypatch.setattr(ingress, "load_policy", lambda *_a, **_k: (grant, ""))
    monkeypatch.setattr(
        ingress,
        "parse_policy",
        lambda *_a, **_k: ((None, head_policy_error) if head_policy_error else (head_grant, "")),
    )
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, changed_paths, None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))
    monkeypatch.setattr(core, "load_ingress_provenance_policy", lambda: (signers, "", None))
    monkeypatch.setattr(core, "load_connector_write_ingress_policy", lambda: connector_policy)

    document: object | None
    if receipt == "default":
        document = _authorization().document()
    else:
        document = receipt

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "pulls/501/commits" in path:
            return commits if commits is not None else [_commit()]
        if path.startswith("pulls/501"):
            return {"head": {"ref": head_ref, "sha": HEAD}, "base": {"ref": "main"}}
        if path.startswith(f"contents/{core.CODE_WRITE_POLICY_RELATIVE_PATH}"):
            # Served so the controller can read the candidate head's own grant;
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
        if "actions/runs" in path:
            return {"workflow_runs": branch_runs if branch_runs is not None else [SUCCESSFUL_BRANCH_RUN]}
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)
    return core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)


import hunter_github_transport as transport  # noqa: E402  (used by the stub above)


def test_exact_authorization_plus_exact_hosted_preflight_is_admitted(monkeypatch) -> None:
    state, _ = _admission(monkeypatch)

    assert state == "success"


def test_connector_commit_without_any_authorization_receipt_fails(monkeypatch) -> None:
    """A signed commit from an allowlisted login never proves it crossed the authorizer."""
    state, description = _admission(monkeypatch, receipt=None)

    assert state == "failure"
    assert "carries no exact-head authorization receipt" in description


def test_connector_commit_touching_a_prohibited_path_fails(monkeypatch) -> None:
    paths = CONTENT_PATHS + (".github/workflows/ci.yml",)
    state, description = _admission(
        monkeypatch,
        changed_paths=paths,
        receipt=_authorization(paths=paths).document(),
    )

    assert state == "failure"
    assert "prohibited path(s)" in description


def test_connector_commit_touching_an_out_of_scope_path_fails(monkeypatch) -> None:
    paths = CONTENT_PATHS + ("pyproject.toml",)
    state, description = _admission(
        monkeypatch,
        changed_paths=paths,
        receipt=_authorization(paths=paths).document(),
    )

    assert state == "failure"
    assert "outside the granted connector scope" in description


def test_receipt_cannot_understate_the_files_the_candidate_actually_changed(monkeypatch) -> None:
    """The scope check runs on trusted PR files, not on the receipt's own list."""
    state, description = _admission(
        monkeypatch,
        changed_paths=CONTENT_PATHS + ("pyproject.toml",),
        receipt=_authorization().document(),
    )

    assert state == "failure"
    assert "do not match the authorized path set" in description


def test_connector_commit_bound_to_the_wrong_issue_fails(monkeypatch) -> None:
    state, description = _admission(monkeypatch, receipt=_authorization(issue="404").document())

    assert state == "failure"
    assert "claims Issue #404 but the branch binds Issue #403" in description


def test_authorization_minted_for_another_branch_cannot_be_replayed(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        receipt=_authorization(target_ref="connector/issue-403-other").document(),
    )

    assert state == "failure"
    assert "not this candidate's" in description


def test_stale_authorization_reused_on_a_newer_head_fails(monkeypatch) -> None:
    """A newer head changed more files, so the older authorization no longer covers it."""
    state, description = _admission(
        monkeypatch,
        changed_paths=CONTENT_PATHS + ("src/hunter/second.py",),
        receipt=_authorization().document(),
    )

    assert state == "failure"
    assert "do not match the authorized path set" in description


def test_authorization_bound_to_a_different_fork_point_fails(monkeypatch) -> None:
    state, description = _admission(monkeypatch, merge_base=ADVANCED_TIP)

    assert state == "failure"
    assert "is not this candidate's fork point" in description


def test_authorization_writer_must_match_every_attested_commit(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        commits=[_commit(), _commit("d" * 40, signer="claude")],
    )

    assert state == "failure"
    assert "carries commits from claude" in description


def test_authorization_naming_an_ungranted_writer_fails(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        commits=[_commit(signer="fafa33")],
        receipt=_authorization(writer="fafa33").document(),
    )

    assert state == "failure"
    assert "is not granted" in description


def test_receipt_on_a_non_connector_branch_fails(monkeypatch) -> None:
    state, description = _admission(monkeypatch, head_ref="claude/issue-403-x", commits=[_commit(signer="claude")])

    assert state == "failure"
    assert "outside the connector namespace" in description


def test_clone_branch_without_a_receipt_is_not_treated_as_connector_origin(monkeypatch) -> None:
    state, _ = _admission(
        monkeypatch,
        head_ref="claude/issue-403-x",
        commits=[_commit(signer="claude")],
        receipt=None,
    )

    assert state == "success"


def test_unreadable_receipt_evidence_fails_closed(monkeypatch) -> None:
    state, description = _admission(monkeypatch, receipt="not-an-object")

    assert state == "failure"
    assert "authorization receipt must be a JSON object" in description


def test_receipt_with_a_forged_identifier_fails(monkeypatch) -> None:
    document = _authorization().document()
    document["authorization_id"] = "0" * 64
    state, description = _admission(monkeypatch, receipt=document)

    assert state == "failure"
    assert "does not match its own claims" in description


# --------------------------------------------------------------------------
# Admission: connector-origin heads stay unadmitted without hosted proof
# --------------------------------------------------------------------------


def test_connector_head_without_hosted_preflight_stays_unadmitted(monkeypatch) -> None:
    state, description = _admission(monkeypatch, statuses=[])

    assert state == "failure"
    assert "requires trusted hosted exact-head canonical preflight proof" in description


def test_connector_head_with_failed_hosted_preflight_stays_unadmitted(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        statuses=[{"id": 9, "context": core._upgrade_status_context(501), "state": "failure"}],
    )

    assert state == "failure"
    assert "requires trusted hosted exact-head canonical preflight proof" in description


def test_connector_head_still_requires_the_exact_head_branch_preflight(monkeypatch) -> None:
    """Trusted hosted proof is additional to the existing exact-head branch gate."""
    state, description = _admission(monkeypatch, branch_runs=[])

    assert state == "failure"
    assert "exact-head branch preflight is missing" in description


def test_hosted_preflight_proof_bound_to_another_pr_does_not_admit(monkeypatch) -> None:
    """Exact-head proof is PR-bound; a status from another PR is not this PR's proof."""
    state, description = _admission(
        monkeypatch,
        statuses=[{"id": 9, "context": core._upgrade_status_context(777), "state": "success"}],
    )

    assert state == "failure"
    assert "exact-head trusted preflight upgrade status is missing" in description


def test_unsigned_connector_commit_is_rejected_before_any_hosted_proof(monkeypatch) -> None:
    unsigned = {
        "sha": HEAD,
        "committer": {"login": CONNECTOR},
        "commit": {"verification": {"verified": False, "reason": "unsigned"}},
    }

    state, description = _admission(monkeypatch, commits=[unsigned])

    assert state == "failure"
    assert "no verified pre-push ingress signature" in description


def test_arbitrary_api_writer_is_rejected_at_admission(monkeypatch) -> None:
    state, description = _admission(monkeypatch, commits=[_commit(signer="drive-by-account")])

    assert state == "failure"
    assert "unauthorized ingress signer drive-by-account" in description


def test_connector_writer_is_not_accepted_while_the_grant_is_disabled(monkeypatch) -> None:
    state, description = _admission(monkeypatch, connector_policy=(False, frozenset(), None))

    assert state == "failure"
    assert "unauthorized ingress signer" in description


def test_shared_identity_still_cannot_pass_as_local_pre_push_proof(monkeypatch) -> None:
    """The connector may write as an account that is also a clone-capable signer.

    Committer login therefore cannot say which channel wrote a commit, and
    demanding disjoint identities would make the grant unbindable rather than
    safer. The safety property is kept as evidence instead: a connector-origin
    head needs its exact authorization re-derived AND the trusted hosted proof,
    and a verified signature alone admits nothing while the grant is active.
    """
    shared = "fafa33"
    shared_grant = _policy(writers=((shared, "feature-branch-write"),))
    bound = {
        "commits": [_commit(signer=shared)],
        "connector_policy": (True, frozenset({shared}), None),
        "policy": shared_grant,
    }

    unproven, description = _admission(
        monkeypatch, receipt=_authorization(grant=shared_grant, writer=shared).document(), statuses=[], **bound
    )
    assert unproven == "failure"
    assert "a verified signature alone is not pre-push proof" in description

    unauthorized, description = _admission(monkeypatch, receipt=None, **bound)
    assert unauthorized == "failure"
    assert "carries no exact-head authorization receipt" in description

    proven, _ = _admission(monkeypatch, receipt=_authorization(grant=shared_grant, writer=shared).document(), **bound)
    assert proven == "success"


def test_active_grant_requires_hosted_proof_for_clone_written_ranges_too(monkeypatch) -> None:
    """Not only for connector-attributed commits: identity cannot be trusted to sort them."""
    state, description = _admission(
        monkeypatch,
        head_ref="claude/issue-403-x",
        commits=[_commit(signer="claude")],
        receipt=None,
        statuses=[],
    )

    assert state == "failure"
    assert "requires trusted hosted exact-head canonical preflight proof" in description


def test_clone_capable_range_is_unaffected_while_no_grant_is_active(monkeypatch) -> None:
    """With the grant inactive, admission stays exactly signature-plus-branch-preflight."""
    state, description = _admission(
        monkeypatch,
        head_ref="claude/issue-403-x",
        commits=[_commit(signer="claude")],
        receipt=None,
        connector_policy=(False, frozenset(), None),
        statuses=[],
    )

    assert state == "success"
    assert "trusted hosted" not in description


def test_malformed_connector_grant_blocks_admission(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        connector_policy=(False, frozenset(), "connector write ingress grant is malformed"),
    )

    assert state == "failure"
    assert "connector write ingress grant is malformed" in description


def test_shipped_grant_is_active_in_the_governance_controller() -> None:
    enabled, logins, error = core.load_connector_write_ingress_policy()

    assert error is None
    assert enabled is True
    assert logins


def test_active_grant_that_drops_the_hosted_proof_requirement_is_refused(monkeypatch, tmp_path: Path) -> None:
    """The declaration is the safety property that replaced identity disjointness."""
    document = _policy_document()
    document["connector_write_ingress"]["hosted_admission"]["require_for_all_candidates"] = False
    monkeypatch.setattr(core, "CODE_WRITE_POLICY_PATH", _forge(tmp_path, document))

    enabled, logins, error = core.load_connector_write_ingress_policy()

    assert enabled is False
    assert logins == frozenset()
    assert error is not None and "hosted exact-head proof for all candidates" in error


def test_branch_issue_is_derived_from_trusted_branch_evidence() -> None:
    policy = _policy()

    assert policy.issue_for_branch("connector/issue-403-example") == "403"
    assert policy.issue_for_branch("connector/issue-40-example") == "40"
    assert policy.issue_for_branch("connector/adhoc") is None
    assert policy.issue_for_branch("claude/issue-403-example") is None
