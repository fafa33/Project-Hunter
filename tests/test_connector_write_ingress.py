"""Deterministic hostile tests for the Issue #403 governed connector write ingress.

Two boundaries are covered:

* `hunter_connector_write_ingress` -- the write-time authorization decision
  (writer identity/capability, direct-main attempt, Issue scope, base provenance
  and staleness, path scope);
* `hunter_governance_review_v2` -- the admission-time consequence, namely that a
  connector-written head stays unadmitted until the trusted hosted exact-head
  canonical preflight proves it, and that connector identity can never be counted
  as clone-capable pre-push proof.
"""

from __future__ import annotations

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

CONNECTOR = "trusted-connector-app[bot]"


def _policy(**overrides: object) -> ingress.ConnectorIngressPolicy:
    policy = ingress.ConnectorIngressPolicy(
        enabled=True,
        writers=((CONNECTOR, "feature-branch-write"),),
        required_capability="feature-branch-write",
        base_ref="main",
        forbidden_target_refs=frozenset({"main", "HEAD"}),
        branch_pattern_template="*/issue-{issue}-*",
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
        target_ref="refs/heads/connector/issue-403-example",
        base_ref="main",
        base_sha=BASE_TIP,
        observed_base_tip_sha=BASE_TIP,
        paths=("src/hunter/example.py", "tests/test_example.py"),
    )
    return replace(request, **overrides)  # type: ignore[arg-type]


def _evaluate(**overrides: object) -> ingress.IngressDecision:
    return ingress.evaluate_write_request(_request(**overrides), _policy())


# --------------------------------------------------------------------------
# Baseline: the governed path must actually authorize a compliant request, so
# every rejection below is a real boundary rather than a gate that refuses all.
# --------------------------------------------------------------------------


def test_compliant_feature_branch_write_is_authorized() -> None:
    decision = _evaluate()

    assert decision.authorized is True
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
    decision = ingress.evaluate_write_request(_request(), _policy(enabled=False))

    assert decision.authorized is False
    assert "not enabled" in decision.reason


def test_enabled_grant_without_a_bound_writer_authorizes_nothing() -> None:
    decision = ingress.evaluate_write_request(_request(), _policy(writers=()))

    assert decision.authorized is False
    assert "binds no writer identity" in decision.reason


def test_missing_policy_fails_closed() -> None:
    decision = ingress.evaluate_write_request(_request(), None, policy_error="policy is missing")

    assert decision.authorized is False
    assert "policy is missing" in decision.reason


# --------------------------------------------------------------------------
# Hostile: direct main attempt
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


def test_base_branch_other_than_the_authorized_base_is_rejected() -> None:
    decision = _evaluate(base_ref="release/3.6", target_ref="connector/issue-403-example")

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


# --------------------------------------------------------------------------
# Hostile: stale base
# --------------------------------------------------------------------------


def test_stale_base_commit_is_rejected() -> None:
    decision = _evaluate(base_sha=STALE_BASE)

    assert decision.authorized is False
    assert decision.reason.startswith("stale base:")


@pytest.mark.parametrize("field", ["base_sha", "observed_base_tip_sha"])
def test_absent_base_provenance_fails_closed(field: str) -> None:
    decision = _evaluate(**{field: ""})

    assert decision.authorized is False
    assert "SHA" in decision.reason


def test_abbreviated_base_sha_is_not_accepted_as_exact_provenance() -> None:
    decision = _evaluate(base_sha=BASE_TIP[:10], observed_base_tip_sha=BASE_TIP[:10])

    assert decision.authorized is False
    assert "full base commit SHA" in decision.reason


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
# The shipped policy must load, stay closed until owner-bound, and never weaken
# the boundaries it sits beside.
# --------------------------------------------------------------------------


def test_shipped_policy_loads_and_is_fail_closed_until_owner_binding() -> None:
    policy, error = ingress.load_policy()

    assert error == ""
    assert policy is not None
    assert policy.local_pre_push_equivalent is False
    assert policy.require_exact_base_tip is True
    assert policy.base_ref == "main"
    assert "main" in policy.forbidden_target_refs
    # The grant is declared but not activated: no writer identity is bound yet, so
    # the ingress authorizes nothing and existing behaviour is unchanged.
    assert policy.enabled is False
    assert policy.writers == ()


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


def test_policy_claiming_local_pre_push_equivalence_is_refused(tmp_path: Path) -> None:
    document = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    document["connector_write_ingress"]["local_pre_push_equivalent"] = True
    forged = tmp_path / "CODE_WRITE_POLICY.json"
    forged.write_text(json.dumps(document), encoding="utf-8")

    policy, error = ingress.load_policy(forged)

    assert policy is None
    assert "local pre-push equivalence" in error


def test_policy_dropping_the_exact_base_tip_requirement_is_refused(tmp_path: Path) -> None:
    document = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    document["connector_write_ingress"]["require_exact_base_tip"] = False
    forged = tmp_path / "CODE_WRITE_POLICY.json"
    forged.write_text(json.dumps(document), encoding="utf-8")

    policy, error = ingress.load_policy(forged)

    assert policy is None
    assert "exact base tip" in error


def test_policy_branch_pattern_without_issue_binding_is_refused(tmp_path: Path) -> None:
    document = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    document["connector_write_ingress"]["branch_pattern_template"] = "connector/*"
    forged = tmp_path / "CODE_WRITE_POLICY.json"
    forged.write_text(json.dumps(document), encoding="utf-8")

    policy, error = ingress.load_policy(forged)

    assert policy is None
    assert "governing Issue" in error


# --------------------------------------------------------------------------
# Admission: connector-written heads stay unadmitted without trusted hosted
# exact-head canonical preflight proof.
# --------------------------------------------------------------------------


def _connector_commit(sha: str = HEAD, signer: str = CONNECTOR) -> dict:
    return {
        "sha": sha,
        "committer": {"login": signer},
        "commit": {"verification": {"verified": True, "reason": "valid"}},
    }


SUCCESSFUL_BRANCH_RUN = {
    "head_sha": HEAD,
    "name": core.PRE_PR_WORKFLOW_NAME,
    "path": core.PRE_PR_WORKFLOW_PATH,
    "event": "push",
    "status": "completed",
    "conclusion": "success",
    "id": 100,
}


def _admission(
    monkeypatch: pytest.MonkeyPatch,
    commits: list[dict],
    *,
    statuses: list[dict] | None = None,
    branch_runs: list[dict] | None = None,
    connector_policy: tuple[bool, frozenset[str], str | None] = (True, frozenset({CONNECTOR}), None),
    signers: frozenset[str] = frozenset({"claude", "fafa33"}),
) -> tuple[str, str]:
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, ("src/hunter/example.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))
    monkeypatch.setattr(core, "load_ingress_provenance_policy", lambda: (signers, "", None))
    monkeypatch.setattr(core, "load_connector_write_ingress_policy", lambda: connector_policy)

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "pulls/501/commits" in path:
            return commits
        if "statuses" in path:
            return statuses if statuses is not None else []
        if "actions/runs" in path:
            return {"workflow_runs": branch_runs if branch_runs is not None else [SUCCESSFUL_BRANCH_RUN]}
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)
    return core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)


def test_connector_written_head_without_hosted_preflight_stays_unadmitted(monkeypatch) -> None:
    state, description = _admission(monkeypatch, [_connector_commit()])

    assert state == "failure"
    assert "connector-written commit(s) require trusted hosted exact-head canonical preflight proof" in description


def test_connector_written_head_with_failed_hosted_preflight_stays_unadmitted(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        [_connector_commit()],
        statuses=[{"id": 9, "context": core._upgrade_status_context(501), "state": "failure"}],
    )

    assert state == "failure"
    assert "connector-written commit(s) require" in description


def test_connector_written_head_with_successful_hosted_preflight_is_admitted(monkeypatch) -> None:
    state, _ = _admission(
        monkeypatch,
        [_connector_commit()],
        statuses=[{"id": 9, "context": core._upgrade_status_context(501), "state": "success"}],
    )

    assert state == "success"


def test_connector_ingress_proof_is_the_trusted_hosted_exact_head_preflight(monkeypatch) -> None:
    monkeypatch.setattr(core, "load_ingress_provenance_policy", lambda: (frozenset({"claude"}), "", None))
    monkeypatch.setattr(core, "load_connector_write_ingress_policy", lambda: (True, frozenset({CONNECTOR}), None))

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "pulls/501/commits" in path:
            return [_connector_commit()]
        if "statuses" in path:
            return [{"id": 9, "context": core._upgrade_status_context(501), "state": "success"}]
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)

    ok, description = core.verify_code_write_ingress_provenance("fafa33/Project-Hunter", "token", HEAD, 501)

    assert ok is True
    assert "trusted hosted exact-head canonical preflight proof" in description


def test_connector_head_still_requires_the_exact_head_branch_preflight(monkeypatch) -> None:
    """Trusted hosted proof is additional to the existing exact-head branch gate."""
    state, description = _admission(
        monkeypatch,
        [_connector_commit()],
        statuses=[{"id": 9, "context": core._upgrade_status_context(501), "state": "success"}],
        branch_runs=[],
    )

    assert state == "failure"
    assert "exact-head branch preflight is missing" in description


def test_hosted_preflight_proof_bound_to_another_pr_does_not_admit(monkeypatch) -> None:
    """Exact-head proof is PR-bound; a status from another PR is not this PR's proof."""
    state, description = _admission(
        monkeypatch,
        [_connector_commit()],
        statuses=[{"id": 9, "context": core._upgrade_status_context(777), "state": "success"}],
    )

    assert state == "failure"
    assert "exact-head trusted preflight upgrade status is missing" in description


def test_stale_exact_head_proof_from_an_earlier_commit_does_not_admit(monkeypatch) -> None:
    """A newer connector commit is not covered by the previous head's proof.

    The trusted status is read for the exact current head, so proof published
    against the superseded head is simply absent here rather than reusable.
    """
    superseded = "d" * 40
    published_against_superseded = {"id": 9, "context": core._upgrade_status_context(501), "state": "success"}

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "pulls/501/commits" in path:
            return [_connector_commit(superseded), _connector_commit(HEAD)]
        if f"commits/{superseded}/statuses" in path:
            return [published_against_superseded]
        if f"commits/{HEAD}/statuses" in path:
            return []
        raise AssertionError(path)

    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, ("src/hunter/example.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))
    monkeypatch.setattr(core, "load_ingress_provenance_policy", lambda: (frozenset({"claude"}), "", None))
    monkeypatch.setattr(core, "load_connector_write_ingress_policy", lambda: (True, frozenset({CONNECTOR}), None))
    monkeypatch.setattr(core, "request_json", fake_request)

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "failure"
    assert "exact-head trusted preflight upgrade status is missing" in description


def test_unsigned_connector_commit_is_rejected_before_any_hosted_proof(monkeypatch) -> None:
    unsigned = {
        "sha": HEAD,
        "committer": {"login": CONNECTOR},
        "commit": {"verification": {"verified": False, "reason": "unsigned"}},
    }

    state, description = _admission(monkeypatch, [unsigned])

    assert state == "failure"
    assert "no verified pre-push ingress signature" in description


def test_arbitrary_api_writer_is_rejected_at_admission(monkeypatch) -> None:
    state, description = _admission(monkeypatch, [_connector_commit(signer="drive-by-account")])

    assert state == "failure"
    assert "unauthorized ingress signer drive-by-account" in description


def test_connector_writer_is_not_accepted_while_the_grant_is_disabled(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        [_connector_commit()],
        connector_policy=(False, frozenset(), None),
    )

    assert state == "failure"
    assert "unauthorized ingress signer" in description


def test_connector_writer_that_is_also_a_clone_signer_blocks_admission(monkeypatch) -> None:
    """A shared identity would let a connector write count as local pre-push proof."""
    state, description = _admission(
        monkeypatch,
        [_connector_commit(signer="claude")],
        connector_policy=(True, frozenset({"claude"}), None),
    )

    assert state == "failure"
    assert "also a clone-capable pre-push signer" in description


def test_malformed_connector_grant_blocks_admission(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        [_connector_commit()],
        connector_policy=(False, frozenset(), "connector write ingress grant is malformed"),
    )

    assert state == "failure"
    assert "connector write ingress grant is malformed" in description


def test_clone_capable_range_is_unaffected_by_the_new_ingress(monkeypatch) -> None:
    """The added path must not change admission for ordinary clone-written work."""
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, ("src/hunter/example.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))
    monkeypatch.setattr(core, "load_ingress_provenance_policy", lambda: (frozenset({"claude"}), "", None))
    monkeypatch.setattr(core, "load_connector_write_ingress_policy", lambda: (True, frozenset({CONNECTOR}), None))

    successful_run = {
        "head_sha": HEAD,
        "name": core.PRE_PR_WORKFLOW_NAME,
        "path": core.PRE_PR_WORKFLOW_PATH,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "id": 100,
    }

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "pulls/501/commits" in path:
            return [_connector_commit(signer="claude")]
        if "actions/runs" in path:
            return {"workflow_runs": [successful_run]}
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)

    state, _ = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "success"


def test_shipped_grant_leaves_the_governance_controller_behaviour_unchanged() -> None:
    enabled, logins, error = core.load_connector_write_ingress_policy()

    assert error is None
    assert enabled is False
    assert logins == frozenset()


def _activated_policy_document(login: str) -> dict:
    document = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    document["connector_write_ingress"]["enabled"] = True
    document["connector_write_ingress"]["authorized_writers"][0]["login"] = login
    return document


def _forge(tmp_path: Path, document: dict) -> Path:
    forged = tmp_path / "CODE_WRITE_POLICY.json"
    forged.write_text(json.dumps(document), encoding="utf-8")
    return forged


def test_owner_activation_binds_exactly_the_declared_writer(tmp_path: Path) -> None:
    policy, error = ingress.load_policy(_forge(tmp_path, _activated_policy_document(CONNECTOR)))

    assert error == ""
    assert policy is not None
    assert policy.enabled is True
    assert policy.writers == ((CONNECTOR, "feature-branch-write"),)


def test_activated_grant_authorizes_only_the_bound_writer(tmp_path: Path) -> None:
    policy, _ = ingress.load_policy(_forge(tmp_path, _activated_policy_document(CONNECTOR)))

    assert ingress.evaluate_write_request(_request(), policy).authorized is True
    assert ingress.evaluate_write_request(_request(writer="someone-else"), policy).authorized is False


@pytest.mark.parametrize("login", ["not a login", "-leading-dash", "trailing-dash-", "a" * 60])
def test_malformed_bound_writer_login_is_refused(tmp_path: Path, login: str) -> None:
    policy, error = ingress.load_policy(_forge(tmp_path, _activated_policy_document(login)))

    assert policy is None
    assert "malformed" in error


def test_duplicate_writer_grant_is_refused(tmp_path: Path) -> None:
    document = _activated_policy_document(CONNECTOR)
    writers = document["connector_write_ingress"]["authorized_writers"]
    writers.append(dict(writers[0]))

    policy, error = ingress.load_policy(_forge(tmp_path, document))

    assert policy is None
    assert "duplicate writer" in error


def test_activated_grant_still_confines_writes_to_the_granted_scope(tmp_path: Path) -> None:
    policy, _ = ingress.load_policy(_forge(tmp_path, _activated_policy_document(CONNECTOR)))

    for hostile in (
        _request(target_ref="main"),
        _request(base_sha=STALE_BASE),
        _request(issue="404"),
        _request(paths=(".githooks/pre-push",)),
        _request(paths=("pyproject.toml",)),
    ):
        assert ingress.evaluate_write_request(hostile, policy).authorized is False


def _write_request_document(tmp_path: Path, **overrides: object) -> Path:
    document = {
        "writer": CONNECTOR,
        "capability": "feature-branch-write",
        "issue": "403",
        "target_ref": "refs/heads/connector/issue-403-example",
        "base_ref": "main",
        "base_sha": BASE_TIP,
        "observed_base_tip_sha": BASE_TIP,
        "paths": ["src/hunter/example.py"],
    }
    document.update(overrides)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_cli_rejects_a_request_the_shipped_grant_does_not_authorize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["hunter_connector_write_ingress.py", "--request", str(_write_request_document(tmp_path))]
    )

    assert ingress.main() == 1
    assert "REJECT" in capsys.readouterr().out


def test_cli_rejects_an_unreadable_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "request.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["hunter_connector_write_ingress.py", "--request", str(broken)])

    assert ingress.main() == 1
    assert "unreadable write request" in capsys.readouterr().out


def test_cli_authorizes_a_compliant_request_against_an_activated_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ingress, "POLICY_PATH", _forge(tmp_path, _activated_policy_document(CONNECTOR)))
    monkeypatch.setattr(
        "sys.argv", ["hunter_connector_write_ingress.py", "--request", str(_write_request_document(tmp_path))]
    )

    assert ingress.main() == 0
    assert "AUTHORIZE" in capsys.readouterr().out


def test_branch_pattern_does_not_admit_a_subpath_of_the_authorized_branch() -> None:
    """Branch matching is pattern matching, not path-scope containment."""
    policy = _policy(branch_pattern_template="connector/issue-{issue}")

    assert ingress.evaluate_write_request(_request(target_ref="connector/issue-403"), policy).authorized is True
    assert ingress.evaluate_write_request(_request(target_ref="connector/issue-403/x"), policy).authorized is False
