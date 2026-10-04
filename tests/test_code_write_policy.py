from __future__ import annotations

import json
from pathlib import Path

import hunter_defect_prevention_preflight as prevention
import pytest

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
    max_seconds = pool["timeout_policy"]["max_seconds"]
    for agent in (a for a in pool["agents"] if a.get("enabled")):
        # A comment-triggered reviewer's ack ceiling is the pool's own declared
        # max_seconds: see `reviewer_chain_worst_case_seconds` and the
        # docstring on this same check in `hunter_pre_ready_review._pool_problems`
        # for why that -- rather than a second hardcoded number here -- is the
        # single source both this test and the loader validate against.
        ack_limit = max_seconds if agent["trigger_method"].startswith("github-pr-comment:") else 90
        assert 1 <= agent["ack_timeout_seconds"] <= ack_limit
        assert agent["review_timeout_seconds"] >= agent["ack_timeout_seconds"]
        assert agent["review_timeout_seconds"] <= max_seconds


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


def test_codex_hard_review_budget_covers_its_observed_hosted_latency() -> None:
    """PR #535 live evidence: a 5-minute budget silently failed

    over Codex before its own normal ~20-30 minute hosted latency (PR #529
    ~29 min, PR #530 ~21 min) could ever complete. The budget is still hard
    bounded -- just no longer shorter than Codex's own real behaviour.
    """

    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    pool = policy["review_progression"]["review_authority"]["reviewer_pool"]
    codex = next(agent for agent in pool["agents"] if agent["id"] == "codex")

    assert codex["review_timeout_seconds"] == pool["timeout_policy"]["max_seconds"]
    assert codex["ack_timeout_seconds"] == 30
    assert codex["review_timeout_seconds"] >= 30 * 60
    assert codex["retryable"] is False
    assert pool["timeout_policy"]["bounded"] is True


# --- ADR 0037 / OD-3: least-privilege Issue-agent publisher and state-ledger grants -------------------


def _issue_agent_errors(monkeypatch, tmp_path, mutator) -> list[str]:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    mutator(policy["code_write_paths"])
    target = tmp_path / "CODE_WRITE_POLICY.json"
    target.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(prevention, "WRITE_POLICY_PATH", target)
    return prevention.validate_code_write_policy()


def _set(path: str, value):
    def mutate(paths):
        node = paths
        *parents, leaf = path.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value

    return mutate


def _drop(path: str):
    def mutate(paths):
        node = paths
        *parents, leaf = path.split(".")
        for key in parents:
            node = node[key]
        del node[leaf]

    return mutate


ISSUE_AGENT_WIDENINGS = {
    "publisher disallowed": _set("issue_agent_publisher.allowed", False),
    "publisher acts from a non-main ref": _set("issue_agent_publisher.actor.ref", "refs/heads/feature"),
    "publisher acts on a re-run attempt": _set("issue_agent_publisher.actor.run_attempt", 2),
    "run attempt coerced from a boolean": _set("issue_agent_publisher.actor.run_attempt", True),
    "unnamed publisher job": _set("issue_agent_publisher.actor.job", " "),
    "writer is not an authorized signer": _set("issue_agent_publisher.writer_login", "not-a-signer"),
    "authentication key instead of signing-only": _set("issue_agent_publisher.signing.key_kind", "authentication"),
    "unverified signatures accepted": _set("issue_agent_publisher.signing.required_github_verification", "any"),
    "publication into main": _set("issue_agent_publisher.target_ref.prefix", "refs/heads/main"),
    "publication into tags": _set("issue_agent_publisher.target_ref.prefix", "refs/tags/issue-"),
    "branch not bound to the Issue": _set("issue_agent_publisher.target_ref.issue_binding", False),
    "short authorization digest": _set("issue_agent_publisher.target_ref.authorization_digest_hex_length", 8),
    "update authority": _set("issue_agent_publisher.operation.update", True),
    "force authority": _set("issue_agent_publisher.operation.force", True),
    "delete authority": _set("issue_agent_publisher.operation.delete", True),
    "tag authority": _set("issue_agent_publisher.operation.tag", True),
    "pull-request authority": _set("issue_agent_publisher.operation.pull_request", True),
    "not create-only": _set("issue_agent_publisher.operation.create_only", False),
    "update coerced from integer zero": _set("issue_agent_publisher.operation.update", 0),
    "extra operation smuggled in": _set("issue_agent_publisher.operation.rename", True),
    "workflows permission granted": _set("issue_agent_publisher.token.workflows", "write"),
    "extra token permission": _set("issue_agent_publisher.token.pull_requests", "write"),
    "token for other repositories": _set("issue_agent_publisher.token.repository_scope", "all-repositories"),
    "more than one commit": _set("issue_agent_publisher.commit_shape.count", 2),
    "model prose in commit metadata": _set("issue_agent_publisher.commit_shape.model_prose_in_metadata", True),
    "commit not bound to the validated unsigned commit": _set(
        "issue_agent_publisher.commit_shape.non_signature_fields_equal", "head_sha"
    ),
    "second path authority": _set("issue_agent_publisher.path_authority", "publisher_allowlist"),
    "boundary names no contract stage": _set("issue_agent_publisher.required_boundary.stage", "made-up-stage"),
    "boundary moved to a different real stage": _set(
        "issue_agent_publisher.required_boundary.stage", "hosted-full-exact-head-proof"
    ),
    "pre-push-safety executed by the publisher": _set("issue_agent_publisher.required_boundary.executor", "publisher"),
    "pre-push-safety not bound to the unsigned commit": _set(
        "issue_agent_publisher.required_boundary.bound_to", "tree_sha"
    ),
    "candidate code with credentials": _set("issue_agent_publisher.candidate_code_with_credentials", True),
    "hooks executed with credentials": _set("issue_agent_publisher.hooks_executed", True),
    "publisher grant missing": _drop("issue_agent_publisher"),
    "ledger grant missing": _drop("issue_agent_state_ledger"),
    "ledger classified as code": _set("issue_agent_state_ledger.classification", "code"),
    "ledger admissible as a candidate": _set("issue_agent_state_ledger.admissible_as_candidate", True),
    "ledger carries source paths": _set("issue_agent_state_ledger.source_paths", True),
    "ledger in an unprotectable custom ref": _set("issue_agent_state_ledger.target_ref_prefix", "refs/hunter/state/"),
    "ledger owns all branches": _set("issue_agent_state_ledger.target_ref_prefix", "refs/heads/"),
    "ledger overlaps candidate branches": _set(
        "issue_agent_state_ledger.target_ref_prefix", "refs/heads/issue-ledger/"
    ),
    "ledger prefix without a namespace boundary": _set(
        "issue_agent_state_ledger.target_ref_prefix", "refs/heads/hunter-state/v1"
    ),
    "ledger force authority": _set("issue_agent_state_ledger.operation.force", True),
    "ledger delete authority": _set("issue_agent_state_ledger.operation.delete", True),
    "anchor without non_fast_forward": _set("issue_agent_state_ledger.anchor.ruleset_rules", ["deletion"]),
    "anchor duplicated rule hides a missing one": _set(
        "issue_agent_state_ledger.anchor.ruleset_rules", ["deletion", "deletion"]
    ),
    "anchor with a bypass actor": _set(
        "issue_agent_state_ledger.anchor.bypass_actors", [{"actor_type": "RepositoryRole", "actor_id": 5}]
    ),
    "ledger written by a personal token": _set("issue_agent_state_ledger.writer.token", "PAT"),
    "ledger written from a feature branch": _set("issue_agent_state_ledger.writer.ref", "refs/heads/feature"),
}


def test_issue_agent_grants_validate_on_the_canonical_policy() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    assert prevention.validate_issue_agent_code_write_paths(policy) == []


@pytest.mark.parametrize("widening", sorted(ISSUE_AGENT_WIDENINGS))
def test_issue_agent_grants_reject_every_widening(monkeypatch, tmp_path, widening) -> None:
    assert _issue_agent_errors(monkeypatch, tmp_path, ISSUE_AGENT_WIDENINGS[widening]) != []


ISSUE_AGENT_EQUIVALENTS = {
    "operation keys reordered": lambda paths: paths["issue_agent_publisher"].update(
        operation=dict(reversed(list(paths["issue_agent_publisher"]["operation"].items())))
    ),
    "anchor rules listed in another order": _set(
        "issue_agent_state_ledger.anchor.ruleset_rules", ["non_fast_forward", "deletion"]
    ),
    "descriptive prose reworded": _set("issue_agent_publisher.purpose", "Narrow create-only Issue-agent publication."),
    "extra descriptive note": _set("issue_agent_state_ledger.note", "Ledger records are public and non-secret."),
}


@pytest.mark.parametrize("equivalent", sorted(ISSUE_AGENT_EQUIVALENTS))
def test_issue_agent_grants_accept_canonically_equivalent_spellings(monkeypatch, tmp_path, equivalent) -> None:
    assert _issue_agent_errors(monkeypatch, tmp_path, ISSUE_AGENT_EQUIVALENTS[equivalent]) == []
