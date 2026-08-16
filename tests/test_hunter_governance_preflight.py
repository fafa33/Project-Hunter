from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import hunter_governance_preflight as preflight
import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "governance" / "issue_276_preflight.json"
HEAD = "a" * 40
BASE = "b" * 40


def fixture_issue() -> preflight.IssueIdentity:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return preflight.IssueIdentity.from_payload("fafa33/Project-Hunter", payload)


def evidence_for(issue: preflight.IssueIdentity) -> dict[str, dict[str, str]]:
    return {
        criterion: {"status": "PASS", "evidence": f"deterministic proof {index}"}
        for index, criterion in enumerate(preflight.issue_acceptance_criteria(issue.body), start=1)
    }


def generated_body(*, ready: bool = True) -> str:
    issue = fixture_issue()
    return preflight.generate_pr_body(
        issue,
        template_text=(ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8"),
        changed_files=("scripts/hunter_governance_preflight.py", "tests/test_hunter_governance_preflight.py"),
        head_sha=HEAD,
        base_sha=BASE,
        evidence=evidence_for(issue) if ready else {},
    )


def test_canonical_governance_loader_resolves_current_repository() -> None:
    preflight.validate_canonical_governance(ROOT)


def test_generator_uses_verified_issue_template_scope_and_complete_matrix() -> None:
    issue = fixture_issue()
    body = generated_body()

    assert "Closes #276" in body
    assert preflight.trace_identity(body) == (276, HEAD, BASE)
    rows = preflight.parse_acceptance_matrix(body)
    assert [row.criterion for row in rows] == list(preflight.issue_acceptance_criteria(issue.body))
    assert {row.status for row in rows} == {"pass"}
    assert preflight.checked_readiness(body) == "ready for review"
    assert "APPROVED" not in body


def test_generator_fails_closed_for_unproven_criteria() -> None:
    body = generated_body(ready=False)

    assert {row.status for row in preflight.parse_acceptance_matrix(body)} == {"blocked"}
    assert preflight.checked_readiness(body) == "changes required"


def test_pr_body_rejects_missing_matrix() -> None:
    issue = fixture_issue()
    body = generated_body().replace("## Acceptance-criteria matrix", "## Acceptance evidence")

    with pytest.raises(preflight.PreflightError, match="matrix"):
        preflight.validate_pr_body(body, issue, head_sha=HEAD, base_sha=BASE, promotion=False)


def test_pr_body_rejects_omitted_issue_criterion() -> None:
    issue = fixture_issue()
    body = generated_body()
    row = preflight.parse_acceptance_matrix(body)[0]
    body = body.replace(f"| {row.criterion} | PASS | {row.evidence} |\n", "")

    with pytest.raises(preflight.PreflightError, match="omits governing Issue"):
        preflight.validate_pr_body(body, issue, head_sha=HEAD, base_sha=BASE, promotion=False)


def test_pr_body_rejects_invented_criterion() -> None:
    issue = fixture_issue()
    body = generated_body().replace(
        "|---|---|---|\n",
        "|---|---|---|\n| Invented criterion | PASS | explicit proof |\n",
    )

    with pytest.raises(preflight.PreflightError, match="not present in governing Issue"):
        preflight.validate_pr_body(body, issue, head_sha=HEAD, base_sha=BASE, promotion=False)


def test_pass_cannot_be_inferred_from_green_ci() -> None:
    issue = fixture_issue()
    body = generated_body()
    first = preflight.parse_acceptance_matrix(body)[0]
    body = body.replace(first.evidence, "CI green", 1)

    with pytest.raises(preflight.PreflightError, match="explicit evidence"):
        preflight.validate_pr_body(body, issue, head_sha=HEAD, base_sha=BASE, promotion=False)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (lambda: generated_body().replace("- [x] `READY FOR REVIEW`", "- [ ] `READY FOR REVIEW`"), "Exactly one"),
        (lambda: generated_body().replace("- [ ] `BLOCKED`", "- [x] `BLOCKED`"), "Exactly one"),
        (
            lambda: generated_body()
            .replace("- [x] `READY FOR REVIEW`", "- [ ] `READY FOR REVIEW`")
            .replace("- [ ] `CHANGES REQUIRED`", "- [x] `CHANGES REQUIRED`"),
            "requires READY FOR REVIEW",
        ),
    ],
)
def test_ready_rejects_invalid_readiness_declarations(body, message: str) -> None:
    issue = fixture_issue()

    with pytest.raises(preflight.PreflightError, match=message):
        preflight.validate_pr_body(body(), issue, head_sha=HEAD, base_sha=BASE, promotion=True)


def test_ready_rejects_blocked_criterion() -> None:
    issue = fixture_issue()
    body = generated_body()
    first = preflight.parse_acceptance_matrix(body)[0]
    body = body.replace(f"| {first.criterion} | PASS |", f"| {first.criterion} | BLOCKED |", 1)

    with pytest.raises(preflight.PreflightError, match="FAIL/BLOCKED"):
        preflight.validate_pr_body(body, issue, head_sha=HEAD, base_sha=BASE, promotion=True)


def test_exact_pair_rejects_source_or_target_change() -> None:
    issue = fixture_issue()
    body = generated_body()

    with pytest.raises(preflight.PreflightError, match="source head"):
        preflight.validate_pr_body(body, issue, head_sha="c" * 40, base_sha=BASE, promotion=True)
    with pytest.raises(preflight.PreflightError, match="target revision"):
        preflight.validate_pr_body(body, issue, head_sha=HEAD, base_sha="d" * 40, promotion=True)


def test_rule_21_rejects_branch_commit_title_and_body_mismatch() -> None:
    issue = fixture_issue()

    with pytest.raises(preflight.PreflightError, match="Branch"):
        preflight.validate_issue_identity(
            issue,
            repository=issue.repository,
            objective=issue.title,
            branch="governance/issue-999-wrong",
        )
    with pytest.raises(preflight.PreflightError, match="Commit message"):
        preflight.validate_issue_identity(
            issue,
            repository=issue.repository,
            objective=issue.title,
            commit_message="feat: no governing issue",
        )
    with pytest.raises(preflight.PreflightError, match="title"):
        preflight.validate_issue_identity(
            issue,
            repository=issue.repository,
            objective=issue.title,
            pr_title="Different work",
        )
    with pytest.raises(preflight.PreflightError, match="exactly one"):
        preflight.validate_issue_identity(
            issue,
            repository=issue.repository,
            objective=issue.title,
            pr_body="Closes #275",
        )


def test_rule_21_rejects_closed_issue(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["state"] = "closed"
    temporary = tmp_path / "closed_issue.json"
    temporary.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(preflight.PreflightError, match="not open"):
        preflight.load_issue("fafa33/Project-Hunter", 276, temporary)


def test_ownership_guard_rejects_review_semantics_in_implementation_owner() -> None:
    added = {
        "docs/HUNTER_IMPLEMENTATION_CONTRACT.md": [
            "Every blocking finding must be classified as isolated or systemic before resolution."
        ]
    }

    with pytest.raises(preflight.PreflightError, match="contribution-review"):
        preflight.validate_ownership_added_lines(added)


def test_ownership_guard_allows_explicit_canonical_owner_consumption() -> None:
    preflight.validate_ownership_added_lines(
        {
            "docs/HUNTER_IMPLEMENTATION_CONTRACT.md": [
                "Consume the systemic result already classified by docs/AI_REVIEW_PROTOCOL.md."
            ]
        }
    )


def test_systemic_finding_requires_durable_guard_and_verifier() -> None:
    incomplete = preflight.FindingResolution(
        finding_id="P2-1",
        severity="blocking",
        classification="systemic",
        classification_evidence="same defect can recur",
        reusable_boundary="PR generator",
        durable_guard_evidence=None,
        verifier_evidence=None,
        resolved=True,
    )

    with pytest.raises(preflight.PreflightError, match="durable reusable hardening"):
        preflight.validate_finding_resolution(incomplete)

    complete = preflight.FindingResolution(
        finding_id="P2-1",
        severity="blocking",
        classification="systemic",
        classification_evidence="same defect can recur",
        reusable_boundary="PR generator",
        durable_guard_evidence="counterfactual regression guard",
        verifier_evidence="independent verifier reproduced failure without guard",
        resolved=True,
    )
    preflight.validate_finding_resolution(complete)


def test_live_ready_rejects_unresolved_review_thread(monkeypatch) -> None:
    issue = fixture_issue()
    state = SimpleNamespace(
        draft=True,
        title=issue.title,
        body=generated_body(),
        head_sha=HEAD,
        base_sha=BASE,
        required=(
            SimpleNamespace(name="Quality Gates", present=True, completed=True, conclusion="success"),
            SimpleNamespace(name="dependency-review", present=True, completed=True, conclusion="success"),
            SimpleNamespace(name="CodeQL", present=True, completed=True, conclusion="success"),
        ),
        governance=SimpleNamespace(state="success"),
        shared_head_pull_requests=(),
        unresolved_thread_ids=("THREAD-1",),
        changes_requested=(),
        unacknowledged_comments=(),
    )

    import hunter_merge_readiness as readiness

    monkeypatch.setattr(readiness, "init_globals", lambda: None)
    monkeypatch.setattr(readiness, "read_current_state", lambda _pr: state)
    monkeypatch.setattr(readiness, "feedback_error", lambda _state: "Unresolved review threads remain: 1.")
    monkeypatch.setattr(
        preflight,
        "_gh_json",
        lambda _args: {"head": {"ref": "governance/issue-276-agent-preflight"}},
    )
    readiness.repo = issue.repository
    readiness.repo_owner = "fafa33"
    readiness.token = "token"

    with pytest.raises(preflight.PreflightError, match="Unresolved review threads"):
        preflight._require_current_state_ready(277, issue)


def test_action_surface_covers_issue_required_mutations() -> None:
    assert set(preflight.ALLOWED_ACTIONS) == {
        "branch",
        "commit",
        "push",
        "pr-create",
        "pr-update",
        "ready",
        "resolve-finding",
        "merge-readiness",
    }
