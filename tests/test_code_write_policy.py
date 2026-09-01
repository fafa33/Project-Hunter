from __future__ import annotations

import json
from pathlib import Path

import hunter_defect_prevention_preflight as prevention

ROOT = Path(__file__).resolve().parents[1]


def test_code_write_policy_forbids_direct_api_code_commits() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    paths = policy["code_write_paths"]

    assert paths["local_git_push"]["allowed"] is True
    assert paths["local_git_push"]["required_boundary"] == ".githooks/pre-push"
    assert paths["github_contents_api"]["allowed"] is False
    assert paths["github_git_data_api"]["allowed"] is False
    assert paths["api_only_agents"]["allowed_role"] == "read-review-metadata-only"


def test_code_write_policy_requires_draft_until_exact_head_admission() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    progression = policy["review_progression"]

    assert progression["unadmitted_head_state"] == "draft"
    assert "exact-head" in progression["ready_requires"]
    assert "Pre-PR Preflight" in progression["ready_requires"]
    assert progression["auto_ready"] is False


def test_defect_prevention_guard_validates_code_write_policy() -> None:
    assert prevention.validate_code_write_policy() == []


def test_code_write_policy_grants_only_a_narrow_connector_write_ingress() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    grant = policy["connector_write_ingress"]

    assert grant["governing_issue"] == "403"
    assert grant["base_ref"] == "main"
    assert "main" in grant["forbidden_target_refs"]
    assert "{issue}" in grant["branch_pattern_template"]
    assert grant["require_exact_base_tip"] is True
    assert grant["local_pre_push_equivalent"] is False
    assert grant["hosted_admission"]["unadmitted_head_state"] == "draft"
    assert grant["hosted_admission"]["auto_ready"] is False
    assert grant["hosted_admission"]["auto_merge"] is False


def test_connector_write_ingress_cannot_write_the_guards_that_bind_it() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    prohibited = policy["connector_write_ingress"]["prohibited_paths"]

    for guarded in prevention.MUST_BE_PROHIBITED_FROM_CONNECTOR_WRITES:
        assert any(prevention.path_matches_scope_entry(guarded, entry) for entry in prohibited)


def test_guard_accepts_an_equivalent_glob_spelling_of_the_prohibited_scope() -> None:
    """A canonically equivalent scope statement must not be rejected as invalid."""
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    policy["connector_write_ingress"]["prohibited_paths"] = [
        ".githooks/**",
        ".github/**",
        "scripts/**",
        "docs/*.json",
        "docs/ADR/**",
        "build_backend/**",
        "requirements/**",
        "pyproject.toml",
    ]

    assert prevention.validate_connector_write_ingress(policy) == []


def test_guard_rejects_a_grant_that_stops_covering_its_own_boundary_files() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    policy["connector_write_ingress"]["prohibited_paths"] = ["docs/ADR/"]

    errors = prevention.validate_connector_write_ingress(policy)

    assert any(".githooks/pre-push" in error for error in errors)
    assert any("docs/CODE_WRITE_POLICY.json" in error for error in errors)


def test_active_grant_separates_connector_proof_from_pre_push_proof_by_evidence() -> None:
    """The connector shares the owner's account, so identity cannot separate the channels.

    The grant must therefore say so and carry the evidence requirement that
    replaces identity disjointness, rather than resting on signature identity.
    """
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    grant = policy["connector_write_ingress"]

    assert grant["enabled"] is True
    assert grant["local_pre_push_equivalent"] is False
    assert grant["provenance_separation"].strip()
    assert grant["hosted_admission"]["require_for_all_candidates"] is True


def test_defect_prevention_guard_validates_the_connector_write_ingress_grant() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))

    assert prevention.validate_connector_write_ingress(policy) == []


def test_guard_rejects_a_grant_that_would_enable_automatic_merge() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    policy["connector_write_ingress"]["hosted_admission"]["auto_merge"] = True

    assert any("automatic merge" in error for error in prevention.validate_connector_write_ingress(policy))


def test_guard_rejects_a_grant_that_permits_writing_main() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    policy["connector_write_ingress"]["forbidden_target_refs"] = []

    assert any("forbid main" in error for error in prevention.validate_connector_write_ingress(policy))


def test_guard_rejects_an_active_grant_that_drops_the_hosted_proof_requirement() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    policy["connector_write_ingress"]["hosted_admission"]["require_for_all_candidates"] = False

    assert any(
        "hosted exact-head proof for all candidates" in error
        for error in prevention.validate_connector_write_ingress(policy)
    )


def test_guard_rejects_an_active_grant_that_states_no_provenance_separation() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    policy["connector_write_ingress"]["provenance_separation"] = ""

    assert any("separated from pre-push" in error for error in prevention.validate_connector_write_ingress(policy))


def test_guard_accepts_a_writer_login_that_overlaps_the_clone_capable_signers() -> None:
    """Disjointness would make the grant unbindable; overlap is handled by evidence."""
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    policy["connector_write_ingress"]["authorized_writers"][0]["login"] = "claude"

    assert prevention.validate_connector_write_ingress(policy) == []


def test_guard_rejects_an_enabled_grant_with_no_bound_writer_identity() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    policy["connector_write_ingress"]["authorized_writers"][0]["login"] = ""

    assert any("binds no writer identity" in error for error in prevention.validate_connector_write_ingress(policy))
