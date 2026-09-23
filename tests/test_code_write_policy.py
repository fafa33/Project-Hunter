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
    assert progression["requires_current_head_review_authority"] is False
    assert progression["external_llm_review_required_for_merge"] is False
    assert progression["requires_current_head_codex_review"] is False
    assert "structured evidence" in progression["finding_resolution"]
    assert "regression test" in progression["finding_resolution"]


def _write_policy(monkeypatch, tmp_path, mutator) -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    mutator(policy)
    target = tmp_path / "CODE_WRITE_POLICY.json"
    target.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(prevention, "WRITE_POLICY_PATH", target)
    assert prevention.validate_code_write_policy() != []


def test_code_write_policy_guard_rejects_mandatory_external_review(monkeypatch, tmp_path) -> None:
    _write_policy(
        monkeypatch,
        tmp_path,
        lambda policy: policy["review_progression"].update(requires_current_head_review_authority=True),
    )


def test_code_write_policy_guard_rejects_a_resolution_contract_without_a_regression_test(monkeypatch, tmp_path) -> None:
    _write_policy(
        monkeypatch,
        tmp_path,
        lambda policy: policy["review_progression"].update(
            finding_resolution="resolved findings must carry structured evidence"
        ),
    )


def test_code_write_policy_declares_codex_primary_with_a_recorded_reason_fallback() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    authority = policy["review_progression"]["review_authority"]

    assert authority["primary"] == "deterministic-governance"
    assert authority["fast_fallback"] == "disabled-until-health-gated"
    assert authority["fallback"] == "none-required"
    assert authority["fallback_requires_recorded_reason"] is True
    expected = {
        "governance=success",
        "trusted_preflight=success",
        "unresolved_thread_count=0",
        "structured_evidence=complete",
    }
    assert expected <= set(authority["fallback_requires_snapshot_gates"])


def test_code_write_policy_declares_an_ordered_reviewer_pool_with_a_last_resort_guard() -> None:
    """The ordered reviewer pool: local free-first, Codex hosted fallback, guard last resort."""
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    pool = policy["review_progression"]["review_authority"]["reviewer_pool"]

    assert pool["last_resort"] == "hunter-guard"
    assert pool["timeout_policy"]["bounded"] is True
    assert pool["timeout_policy"]["default_seconds"] > 0
    assert pool["timeout_policy"]["max_seconds"] >= pool["timeout_policy"]["default_seconds"]
    by_id = {agent["id"]: agent for agent in pool["agents"]}
    assert by_id["codex"]["enabled"] is True
    assert by_id["local-ollama"]["priority"] > by_id["groq"]["priority"]
    assert by_id["local-ollama"]["enabled"] is False
    assert by_id["local-ollama"]["authority_eligible"] is False
    assert by_id["codex"]["priority"] == 1
    assert by_id["codex"]["exact_head_support"] is True


def test_reviewer_requires_distinct_ack_and_review_budgets() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    pool = policy["review_progression"]["review_authority"]["reviewer_pool"]
    for agent in (a for a in pool["agents"] if a.get("enabled")):
        ack_limit = 300 if agent["trigger_method"].startswith("github-pr-comment:") else 90
        assert 1 <= agent["ack_timeout_seconds"] <= ack_limit
        assert agent["review_timeout_seconds"] >= agent["ack_timeout_seconds"]


def test_code_write_policy_guard_rejects_a_pool_without_a_strict_last_resort(monkeypatch, tmp_path) -> None:
    _write_policy(
        monkeypatch,
        tmp_path,
        lambda policy: policy["review_progression"]["review_authority"]["reviewer_pool"].pop("last_resort"),
    )


def test_code_write_policy_guard_rejects_required_external_fallback(monkeypatch, tmp_path) -> None:
    _write_policy(
        monkeypatch,
        tmp_path,
        lambda policy: policy["review_progression"]["review_authority"].update(fallback="hunter-guard"),
    )


def test_code_write_policy_guard_rejects_an_unbounded_timeout_policy(monkeypatch, tmp_path) -> None:
    _write_policy(
        monkeypatch,
        tmp_path,
        lambda policy: policy["review_progression"]["review_authority"]["reviewer_pool"]["timeout_policy"].update(
            bounded=False
        ),
    )


def test_code_write_policy_guard_rejects_an_enabled_agent_without_exact_head_support(monkeypatch, tmp_path) -> None:
    _write_policy(
        monkeypatch,
        tmp_path,
        lambda policy: [
            agent.update(exact_head_support=False)
            for agent in policy["review_progression"]["review_authority"]["reviewer_pool"]["agents"]
            if agent["enabled"]
        ],
    )


def test_code_write_policy_guard_rejects_a_pool_without_the_codex_primary(monkeypatch, tmp_path) -> None:
    _write_policy(
        monkeypatch,
        tmp_path,
        lambda policy: policy["review_progression"]["review_authority"]["reviewer_pool"].update(
            agents=[
                agent
                for agent in policy["review_progression"]["review_authority"]["reviewer_pool"]["agents"]
                if agent["id"] != "codex"
            ]
        ),
    )


def test_code_write_policy_guard_rejects_a_policy_without_a_review_authority_model(monkeypatch, tmp_path) -> None:
    _write_policy(
        monkeypatch,
        tmp_path,
        lambda policy: policy["review_progression"].pop("review_authority"),
    )


def test_code_write_policy_guard_rejects_a_fallback_without_a_recorded_reason_requirement(
    monkeypatch, tmp_path
) -> None:
    _write_policy(
        monkeypatch,
        tmp_path,
        lambda policy: policy["review_progression"]["review_authority"].pop("fallback_requires_recorded_reason"),
    )


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
    # The grant binds one entry per capability, so "no bound writer" means every
    # entry is unbound, not merely the first.
    for entry in policy["connector_write_ingress"]["authorized_writers"]:
        entry["login"] = ""

    assert any("binds no writer identity" in error for error in prevention.validate_connector_write_ingress(policy))


def test_local_ollama_is_triage_only_and_disabled_without_health_admission() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    pool = policy["review_progression"]["review_authority"]["reviewer_pool"]
    by_id = {agent["id"]: agent for agent in pool["agents"]}
    local = by_id["local-ollama"]
    assert local["enabled"] is False
    assert local["authority_eligible"] is False
    assert local["retryable"] is False
    assert local["priority"] > by_id["groq"]["priority"]
    assert local["trigger_method"] == "github-workflow:hunter-local-reviewer.yml"
    assert local["ack_timeout_seconds"] == 30
    assert by_id["codex"]["priority"] == 1


def test_codex_hard_review_budget_is_capped_at_five_minutes() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    pool = policy["review_progression"]["review_authority"]["reviewer_pool"]
    codex = next(agent for agent in pool["agents"] if agent["id"] == "codex")

    assert codex["review_timeout_seconds"] == 300
    assert codex["ack_timeout_seconds"] == 300
    assert codex["retryable"] is False
    assert pool["timeout_policy"]["max_seconds"] == 300
