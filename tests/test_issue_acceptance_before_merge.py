"""Issue acceptance before merge: owner-registered Issues only, evidence from existing trusted producers.

The registry (CODE_WRITE_POLICY ... acceptance_requirements) is a policy registry, not a proof authority. These tests pin
the invariants: unregistered changes are untouched, only trusted evidence at the exact head proves acceptance, PR prose,
branch names, closure and generic CI never do, and a production-only exception is never completed acceptance.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import hunter_governance_review_v2 as governance
import hunter_merge_readiness_v2 as readiness
import hunter_pre_ready_review as pre_ready
import pytest

ROOT = Path(__file__).resolve().parents[1]
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
PROOF = ".github/workflows/hunter-issue-682-proof.yml"
SAFETY = ".github/workflows/hunter-issue-682-safety.yml"
TRUST_ROOT_PATH = "config/issue_agent_trust_roots.json"
TRUST_ROOT_BYTES = b'{"roots": 1}'
TRUST_ROOT_SHA = hashlib.sha256(TRUST_ROOT_BYTES).hexdigest()
REPOSITORY = "fafa33/Project-Hunter"


def evidence(ident: str, workflow: str, event: str = "workflow_run") -> dict[str, str]:
    return {"id": ident, "kind": "trusted_workflow_run", "workflow": workflow, "event": event}


def requirement(issue: str = "682", paths: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "issue": issue,
        "governed_paths": paths or ["src/hunter/automation/"],
        "evidence": [evidence("proof", PROOF), evidence("safety", SAFETY)],
    }
    entry.update(extra)
    return entry


def exception(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "authorized_by": "fafa33",
        "authorization": "Issue #682 owner decision 2026-11-06",
        "technical_necessity": "The production trust-root run can only exist after the change is on main.",
        "post_merge_verification": "One bounded canary cycle verified against the signed ledger.",
        "head_sha": HEAD,
        "trust_root": {"path": TRUST_ROOT_PATH, "sha256": TRUST_ROOT_SHA},
        "deferred_evidence": ["proof"],
        "deferred_criteria": [],
        "expires_at": "2026-11-07T00:00:00Z",
    }
    value.update(overrides)
    return value


def policy(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"review_progression": {"review_authority": {"acceptance_requirements": list(entries)}}}


def registry(*entries: dict[str, Any]) -> tuple[pre_ready.AcceptanceRequirement, ...]:
    loaded, problem = pre_ready.load_acceptance_registry(policy(*entries))
    assert loaded is not None, problem
    return loaded


def run(workflow: str, head: str = HEAD, conclusion: str = "success", event: str = "workflow_run", ident: int = 1):
    return {
        "id": ident,
        "run_number": ident,
        "path": workflow,
        "event": event,
        "head_sha": head,
        "status": "completed",
        "conclusion": conclusion,
    }


def evaluate(
    entries: tuple[pre_ready.AcceptanceRequirement, ...],
    paths: tuple[str, ...],
    runs: list[dict[str, Any]] | None,
    *,
    root: str | None = TRUST_ROOT_SHA,
    now: datetime = NOW,
    head: str = HEAD,
) -> tuple[str, str]:
    return governance.evaluate_issue_acceptance(
        entries,
        paths,
        head,
        runs_for=lambda spec: None if runs is None else [r for r in runs if r["path"] == spec.workflow],
        trust_root_sha256=lambda _path: root,
        now=now,
    )


GOVERNED = ("src/hunter/automation/issue_agent_control.py",)
BOTH = [run(PROOF), run(SAFETY, ident=2)]


# --- registered and unregistered Issues ---------------------------------------------------------------------


def test_the_committed_policy_registers_nothing_so_existing_behaviour_is_unchanged() -> None:
    loaded, problem = pre_ready.load_acceptance_registry()
    assert (loaded, problem) == ((), "")


def test_unregistered_changes_are_not_governed_and_need_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pre_ready, "load_acceptance_registry", lambda *_a, **_k: ((), ""))

    def forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("an empty registry must cost no GitHub read")

    monkeypatch.setattr(governance, "request_json", forbidden)
    state, detail = governance.issue_acceptance_state(REPOSITORY, "token", HEAD, 7)
    assert (state, detail.split(":")[0]) == ("success", "ACCEPTANCE_NOT_GOVERNED")


def test_a_registered_issue_does_not_govern_unrelated_paths() -> None:
    state, detail = evaluate(registry(requirement()), ("docs/unrelated.md",), runs=[])
    assert (state, detail.split(":")[0]) == ("success", "ACCEPTANCE_NOT_GOVERNED")


# --- valid and invalid evidence ----------------------------------------------------------------------------


def test_acceptance_is_proven_only_by_every_required_producer_at_the_exact_head() -> None:
    state, detail = evaluate(registry(requirement()), GOVERNED, BOTH)
    assert (state, detail.split(":")[0]) == ("success", "ACCEPTANCE_PROVEN")


def test_missing_evidence_prevents_a_positive_decision() -> None:
    state, detail = evaluate(registry(requirement()), GOVERNED, [run(PROOF)])
    assert state == "pending" and detail.startswith("ACCEPTANCE_MISSING") and "safety" in detail


def test_a_failed_producer_run_is_not_proof() -> None:
    state, detail = evaluate(
        registry(requirement()), GOVERNED, [run(PROOF, conclusion="failure"), run(SAFETY, ident=2)]
    )
    assert state == "pending" and detail.startswith("ACCEPTANCE_MISSING")


def test_a_newer_unfinished_run_is_not_masked_by_an_older_success() -> None:
    older_success = run(PROOF, ident=1)
    newer_running = {**run(PROOF, ident=2), "status": "in_progress", "conclusion": None}
    state, detail = evaluate(registry(requirement()), GOVERNED, [older_success, newer_running, run(SAFETY, ident=3)])
    assert state == "pending" and detail.startswith("ACCEPTANCE_MISSING") and "proof" in detail


def test_a_newer_failed_run_is_not_masked_by_an_older_success() -> None:
    newer_failed = run(PROOF, ident=2, conclusion="failure")
    state, detail = evaluate(
        registry(requirement()), GOVERNED, [run(PROOF, ident=1), newer_failed, run(SAFETY, ident=3)]
    )
    assert state == "pending" and detail.startswith("ACCEPTANCE_MISSING")


def test_an_incomplete_latest_run_is_not_proof() -> None:
    pending = {**run(PROOF), "status": "in_progress", "conclusion": None}
    state, detail = evaluate(registry(requirement()), GOVERNED, [pending, run(SAFETY, ident=2)])
    assert state == "pending" and detail.startswith("ACCEPTANCE_MISSING")


def test_evidence_for_another_head_is_stale() -> None:
    state, detail = evaluate(registry(requirement()), GOVERNED, [run(PROOF, head=OTHER_HEAD), run(SAFETY, ident=2)])
    assert state == "pending" and detail.startswith("ACCEPTANCE_STALE")


def test_a_run_of_the_wrong_event_is_never_counted() -> None:
    wrong = [run(PROOF, event="pull_request"), run(SAFETY, ident=2)]
    state, detail = evaluate(registry(requirement()), GOVERNED, wrong)
    assert state == "pending" and detail.startswith("ACCEPTANCE_MISSING")


def test_a_generic_successful_check_is_not_acceptance_proof() -> None:
    generic = [run(".github/workflows/quality-gates.yml"), run(".github/workflows/codeql.yml", ident=2)]
    state, detail = evaluate(registry(requirement()), GOVERNED, generic)
    assert state == "pending" and detail.startswith("ACCEPTANCE_MISSING")


def test_unavailable_evidence_is_unresolved_not_success() -> None:
    state, detail = evaluate(registry(requirement()), GOVERNED, None)
    assert state == "pending" and detail.startswith("ACCEPTANCE_UNRESOLVED")


def test_an_evidence_producer_modified_by_the_change_cannot_vouch_for_itself() -> None:
    state, detail = evaluate(registry(requirement()), (*GOVERNED, PROOF), BOTH)
    assert state == "failure" and detail.startswith("ACCEPTANCE_UNRESOLVED") and PROOF in detail


# --- conflicting Issue bindings ------------------------------------------------------------------------------


def test_a_change_bound_to_two_governed_issues_is_ambiguous() -> None:
    entries = registry(requirement("682"), requirement("700", paths=["scripts/"]))
    state, detail = evaluate(entries, (*GOVERNED, "scripts/x.py"), BOTH)
    assert state == "failure" and detail.startswith("ACCEPTANCE_AMBIGUOUS") and "#682" in detail and "#700" in detail


# --- forged PR claims, closed Issues, branch names ---------------------------------------------------------


def _stub_github(monkeypatch: pytest.MonkeyPatch, entries: tuple[Any, ...], *, pr: dict[str, Any], runs: list[Any]):
    seen: list[str] = []
    monkeypatch.setattr(pre_ready, "load_acceptance_registry", lambda *_a, **_k: (entries, ""))
    monkeypatch.setattr(
        governance,
        "read_pr_changed_files",
        lambda *_a: (True, (governance.PullRequestFile("modified", GOVERNED[0]),), None),
    )
    monkeypatch.setattr(governance, "read_pr_refs", lambda *_a: (True, pr["head"]["ref"], "main", None))

    def request(_repo: str, _token: str, _method: str, path: str, *_a: Any, **_k: Any) -> Any:
        seen.append(path)
        if path.startswith("actions/workflows/"):
            return {"workflow_runs": runs}
        raise AssertionError(f"unexpected read {path}")

    monkeypatch.setattr(governance, "request_json", request)
    return seen


def live_run(workflow: str, **overrides: Any) -> dict[str, Any]:
    return {**run(workflow), "head_repository": {"full_name": REPOSITORY}, **overrides}


FORGED_PR = {
    "head": {"ref": "issue-682-acceptance"},
    "title": "Closes #682",
    "body": "Fixes #682. Acceptance: PROVEN, verified by reviewer. ACCEPTANCE_PROVEN",
}


def test_forged_pr_claims_and_branch_names_prove_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_github(monkeypatch, registry(requirement()), pr=FORGED_PR, runs=[])
    state, detail = governance.issue_acceptance_state(REPOSITORY, "token", HEAD, 7, now=NOW)
    assert state == "pending" and detail.startswith("ACCEPTANCE_MISSING")
    assert not any(path.startswith(("issues/", "pulls/")) for path in seen), "Issue and PR prose are never read"


def test_a_branch_named_for_an_issue_does_not_bind_unrelated_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = registry(requirement("682", paths=["docs/never-touched/"]))
    _stub_github(monkeypatch, entries, pr=FORGED_PR, runs=[])
    state, detail = governance.issue_acceptance_state(REPOSITORY, "token", HEAD, 7, now=NOW)
    assert (state, detail.split(":")[0]) == ("success", "ACCEPTANCE_NOT_GOVERNED")


def test_a_closed_issue_without_proof_does_not_satisfy_acceptance(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_github(monkeypatch, registry(requirement()), pr=FORGED_PR, runs=[])
    state, _detail = governance.issue_acceptance_state(REPOSITORY, "token", HEAD, 7, now=NOW)
    assert state == "pending"
    assert not any(path.startswith("issues/") for path in seen)


def test_runs_from_another_repository_are_not_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    foreign = [live_run(PROOF, head_repository={"full_name": "evil/Project-Hunter"}), live_run(SAFETY, id=2)]
    _stub_github(monkeypatch, registry(requirement()), pr=FORGED_PR, runs=foreign)
    state, detail = governance.issue_acceptance_state(REPOSITORY, "token", HEAD, 7, now=NOW)
    assert state == "pending" and detail.startswith("ACCEPTANCE_MISSING")


def test_live_adapter_accepts_exact_head_trusted_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = [live_run(PROOF), live_run(SAFETY, id=2, run_number=2)]
    _stub_github(monkeypatch, registry(requirement()), pr=FORGED_PR, runs=runs)
    state, detail = governance.issue_acceptance_state(REPOSITORY, "token", HEAD, 7, now=NOW)
    assert (state, detail.split(":")[0]) == ("success", "ACCEPTANCE_PROVEN")


# --- criteria that cannot be verified mechanically -----------------------------------------------------------


def test_unverifiable_criteria_stay_unresolved_never_success() -> None:
    entries = registry(requirement(unverifiable_criteria=["Kilo edits a bounded Issue in production"]))
    state, detail = evaluate(entries, GOVERNED, BOTH)
    assert state == "failure" and detail.startswith("ACCEPTANCE_UNRESOLVED")


# --- production-only exception ------------------------------------------------------------------------------


def deferred_entry(**exception_overrides: Any) -> tuple[pre_ready.AcceptanceRequirement, ...]:
    return registry(
        requirement(
            unverifiable_criteria=["Kilo edits a bounded Issue in production"],
            production_only=exception(
                deferred_criteria=["Kilo edits a bounded Issue in production"], **exception_overrides
            ),
        )
    )


def test_a_legitimate_production_only_exception_is_pending_never_completed() -> None:
    state, detail = evaluate(deferred_entry(), GOVERNED, [run(SAFETY, ident=2)])
    assert state == "success"
    assert detail.startswith("ACCEPTANCE_PRODUCTION_ONLY_PENDING")
    assert "NOT complete" in detail and "ACCEPTANCE_PROVEN" not in detail and "fafa33" in detail


def test_the_exception_still_requires_the_pre_merge_safety_evidence() -> None:
    state, detail = evaluate(deferred_entry(), GOVERNED, [])
    assert state == "pending" and detail.startswith("ACCEPTANCE_MISSING") and "safety" in detail


@pytest.mark.parametrize(
    ("overrides", "root", "now", "prefix"),
    [
        ({"head_sha": OTHER_HEAD}, TRUST_ROOT_SHA, NOW, "ACCEPTANCE_STALE"),
        ({}, "0" * 64, NOW, "ACCEPTANCE_STALE"),
        ({}, None, NOW, "ACCEPTANCE_UNRESOLVED"),
        ({}, TRUST_ROOT_SHA, datetime(2026, 11, 8, tzinfo=UTC), "ACCEPTANCE_MISSING"),
    ],
)
def test_the_exception_is_bound_to_the_exact_head_trust_root_and_a_bounded_window(
    overrides: dict[str, Any], root: str | None, now: datetime, prefix: str
) -> None:
    state, detail = evaluate(deferred_entry(**overrides), GOVERNED, [run(SAFETY, ident=2)], root=root, now=now)
    assert state == "pending" and detail.startswith(prefix)


def test_unauthorized_deferral_without_an_exception_is_blocked() -> None:
    entries = registry(requirement())  # no production_only record
    state, detail = evaluate(entries, GOVERNED, [run(SAFETY, ident=2)])
    assert state == "pending" and detail.startswith("ACCEPTANCE_MISSING") and "proof" in detail


def test_an_exception_cannot_defer_criteria_it_does_not_name() -> None:
    entries = registry(
        requirement(
            unverifiable_criteria=["Criterion A", "Criterion B"],
            production_only=exception(deferred_criteria=["Criterion A"]),
        )
    )
    state, detail = evaluate(entries, GOVERNED, [run(SAFETY, ident=2)])
    assert state == "failure" and detail.startswith("ACCEPTANCE_UNRESOLVED")


def test_post_merge_acceptance_stays_pending_and_lapses_into_a_block_after_the_bound() -> None:
    after_merge = evaluate(deferred_entry(), GOVERNED, [run(SAFETY, ident=2)])  # still inside the window
    assert after_merge[1].startswith("ACCEPTANCE_PRODUCTION_ONLY_PENDING")
    lapsed = evaluate(deferred_entry(), GOVERNED, [run(SAFETY, ident=2)], now=datetime(2026, 11, 9, tzinfo=UTC))
    assert lapsed[0] == "pending" and lapsed[1].startswith("ACCEPTANCE_MISSING")


def test_when_the_deferred_evidence_really_exists_it_is_proven_not_an_exception() -> None:
    entries = registry(requirement(production_only=exception()))
    state, detail = evaluate(entries, GOVERNED, BOTH)
    assert state == "success" and detail.startswith("ACCEPTANCE_PROVEN")


# --- the registry loader fails closed ----------------------------------------------------------------------


def _bad(mutate: Any) -> tuple[Any, str]:
    entry = copy.deepcopy(requirement(production_only=exception()))
    mutate(entry)
    return pre_ready.load_acceptance_registry(policy(entry))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.update(issue="not-a-number"),
        lambda e: e.update(governed_paths=[]),
        lambda e: e.update(governed_paths=["/etc/passwd"]),
        lambda e: e.update(extra="x"),
        lambda e: e["evidence"].append(evidence("proof", PROOF)),  # duplicate id
        lambda e: e["evidence"].__setitem__(0, {**evidence("proof", PROOF), "kind": "llm_review"}),
        lambda e: e["evidence"].__setitem__(0, evidence("proof", "scripts/not_a_workflow.py")),
        lambda e: e["evidence"].__setitem__(0, evidence("proof", PROOF, event="pull_request")),
        lambda e: e["production_only"].pop("expires_at"),
        lambda e: e["production_only"].update(expires_at="tomorrow"),
        lambda e: e["production_only"].update(head_sha="main"),
        lambda e: e["production_only"].update(authorized_by=""),
        lambda e: e["production_only"].update(trust_root={"path": "../x", "sha256": TRUST_ROOT_SHA}),
        lambda e: e["production_only"].update(deferred_evidence=["undeclared"]),
        lambda e: e["production_only"].update(deferred_evidence=[], deferred_criteria=[]),
    ],
)
def test_a_malformed_registry_is_never_partially_honoured(mutate: Any) -> None:
    loaded, problem = _bad(mutate)
    assert loaded is None and problem


def test_a_duplicate_governing_issue_is_rejected() -> None:
    loaded, problem = pre_ready.load_acceptance_registry(policy(requirement("682"), requirement("682")))
    assert loaded is None and "exactly once" in problem


def test_a_malformed_registry_blocks_the_live_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pre_ready, "load_acceptance_registry", lambda *_a, **_k: (None, "broken"))
    state, detail = governance.issue_acceptance_state(REPOSITORY, "token", HEAD, 7)
    assert state == "failure" and detail.startswith("ACCEPTANCE_UNRESOLVED")


# --- Merge Readiness: one consumed result, ordinary behaviour unchanged ---------------------------------------


def green_checks() -> tuple[dict[str, Any], ...]:
    return tuple({"name": name, "status": "completed", "conclusion": "success"} for name in readiness.REQUIRED_CHECKS)


def decide(acceptance: tuple[str, str] | None = None, **overrides: Any) -> readiness.Decision:
    kwargs: dict[str, Any] = {"check_runs": green_checks(), "knowledge_receipt": ("success", "")}
    if acceptance is not None:
        kwargs["issue_acceptance"] = acceptance
    kwargs.update(overrides)
    return readiness.evaluate(readiness.StaticReadinessObservation(**kwargs))


def test_ordinary_readiness_is_unchanged_without_a_governed_issue() -> None:
    baseline = decide()
    explicit = decide(("success", "ACCEPTANCE_NOT_GOVERNED: no Issue is registered."))
    assert baseline.state == "success" and explicit == baseline


@pytest.mark.parametrize(
    ("acceptance", "state"),
    [
        (("pending", "ACCEPTANCE_MISSING: Issue #682 required evidence is absent (proof)."), "pending"),
        (("pending", "ACCEPTANCE_STALE: Issue #682 evidence does not match the current head."), "pending"),
        (("failure", "ACCEPTANCE_AMBIGUOUS: the change binds more than one governed Issue."), "failure"),
        (("failure", "ACCEPTANCE_UNRESOLVED: criteria cannot be verified mechanically."), "failure"),
    ],
)
def test_missing_stale_ambiguous_or_unresolved_acceptance_prevents_a_positive_decision(
    acceptance: tuple[str, str], state: str
) -> None:
    decision = decide(acceptance)
    assert decision.state == state and acceptance[1] in decision.description


def test_a_production_only_exception_is_ready_but_never_described_as_accepted() -> None:
    decision = decide(("success", "ACCEPTANCE_PRODUCTION_ONLY_PENDING: Issue #682 acceptance is NOT complete."))
    assert decision.state == "success"
    assert "Issue acceptance NOT complete" in decision.description
    assert "accepted" not in decision.description.lower()


def test_the_published_status_keeps_the_not_complete_warning_within_githubs_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = decide(("success", "ACCEPTANCE_PRODUCTION_ONLY_PENDING: Issue #682 acceptance is NOT complete."))
    posted: list[dict[str, Any]] = []

    def request(method: str, path: str, payload: dict[str, Any] | None = None, **_k: Any) -> Any:
        if method == "GET":
            return {"statuses": []}
        posted.append(payload or {})
        return {}

    monkeypatch.setattr(readiness, "request_json", request)
    readiness.publish("c" * 40, decision)
    (published,) = posted
    assert len(published["description"]) <= 140
    assert "NOT complete" in published["description"] and "production-only" in published["description"]


def test_acceptance_never_masks_a_draft_or_a_failing_required_check() -> None:
    proven = ("success", "ACCEPTANCE_PROVEN: ok")
    assert decide(proven, draft=True).state == "pending"
    failed = tuple({**c, "conclusion": "failure"} for c in green_checks())
    assert decide(proven, check_runs=failed).state == "failure"


# --- one canonical proof authority ------------------------------------------------------------------------


def test_merge_readiness_only_consumes_the_result_and_holds_no_evidence_logic() -> None:
    source = (ROOT / "scripts" / "hunter_merge_readiness_v2.py").read_text(encoding="utf-8")
    for forbidden in ("load_acceptance_registry", "workflow_runs", "acceptance_requirements", "trust_root"):
        assert forbidden not in source, f"evidence evaluation must stay in the governance path: {forbidden}"


def test_the_evaluator_is_defined_exactly_once_in_the_governance_path() -> None:
    owners = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owners += [
            path.name
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "evaluate_issue_acceptance"
        ]
    assert owners == ["hunter_governance_review_v2.py"]


def test_the_committed_policy_still_parses_as_json_with_the_registry_beside_coverage_scopes() -> None:
    document = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    authority = document["review_progression"]["review_authority"]
    assert authority["acceptance_requirements"] == [] and "coverage_scopes" in authority
