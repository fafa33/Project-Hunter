from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


def canonical_template() -> str:
    return (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")


def generated_body(*, ready: bool = True, template_text: str | None = None) -> str:
    issue = fixture_issue()
    with patch.object(preflight, "_git_exact_pair", return_value=(HEAD, BASE)):
        body = preflight.generate_pr_body(
            issue,
            repo_root=ROOT,
            base_ref="main",
            template_text=template_text or canonical_template(),
            changed_files=("scripts/hunter_governance_preflight.py", "tests/test_hunter_governance_preflight.py"),
            evidence=evidence_for(issue) if ready else {},
            verification=("ruff check .: PASS",) if ready else (),
            operational_evidence=("Governance-only validation: PASS",) if ready else (),
        )
    if not ready:
        return body
    body = body.replace("- [ ] `READY FOR REVIEW`", "- [x] `READY FOR REVIEW`", 1)
    body = body.replace("- [x] `CHANGES REQUIRED`", "- [ ] `CHANGES REQUIRED`", 1)
    body = body.replace(
        "## Verification\n\n",
        "## Verification\n\n<!-- hunter-verification:v1 ruff=pass black=pass mypy=pass pytest=pass -->\n\n",
        1,
    )
    body = body.replace(
        "## Operational validation\n\n",
        "## Operational validation\n\n<!-- hunter-operational-validation:v1 outcome=not-applicable -->\n\n",
        1,
    )
    return body


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
    assert "- [x] `APPROVED`" not in body


def test_generator_preserves_canonical_template_checklists_and_instructions() -> None:
    body = generated_body()

    assert "The governing Issue and acceptance criteria are linked." in body
    assert "Required migrations/configuration checks" in body
    assert "Green CI and the absence of unresolved blocking review comments" in body
    assert "Replace with criterion" not in body
    assert "Describe the exact issue/milestone scope" not in body


def test_generator_preserves_future_template_content_instead_of_hard_coding_body() -> None:
    template = canonical_template().replace(
        "## Verification\n\n",
        "## Verification\n\nCanonical future template sentinel.\n\n",
        1,
    )

    body = generated_body(template_text=template)

    assert "Canonical future template sentinel." in body


def test_generator_fails_closed_for_unproven_criteria() -> None:
    body = generated_body(ready=False)

    assert {row.status for row in preflight.parse_acceptance_matrix(body)} == {"blocked"}
    assert preflight.checked_readiness(body) == "changes required"


def test_generator_does_not_declare_ready_when_verification_or_operational_evidence_is_missing() -> None:
    issue = fixture_issue()
    with patch.object(preflight, "_git_exact_pair", return_value=(HEAD, BASE)):
        body = preflight.generate_pr_body(
            issue,
            repo_root=ROOT,
            base_ref="main",
            template_text=canonical_template(),
            changed_files=("scripts/hunter_governance_preflight.py",),
            evidence=evidence_for(issue),
        )

    assert preflight.checked_readiness(body) == "changes required"
    assert "Pending exact command/result evidence." in body


def test_generator_never_self_promotes_from_caller_supplied_evidence() -> None:
    issue = fixture_issue()
    fabricated = {
        criterion: {"status": "PASS", "evidence": "fabricated"}
        for criterion in preflight.issue_acceptance_criteria(issue.body)
    }
    with patch.object(preflight, "_git_exact_pair", return_value=(HEAD, BASE)):
        body = preflight.generate_pr_body(
            issue,
            repo_root=ROOT,
            base_ref="main",
            template_text=canonical_template(),
            changed_files=("scripts/hunter_governance_preflight.py",),
            evidence=fabricated,
            verification=("fabricated",),
            operational_evidence=("fabricated",),
        )

    assert preflight.checked_readiness(body) == "changes required"


def test_ready_body_rejects_unchecked_or_placeholder_evidence() -> None:
    issue = fixture_issue()
    body = generated_body()
    unchecked = body.replace("- [x] `ruff check .`", "- [ ] `ruff check .`", 1)
    placeholder = body.replace("ruff check .: PASS", "Pending exact command/result evidence.", 1)

    with pytest.raises(preflight.PreflightError, match="every Verification item complete"):
        preflight.validate_pr_body(unchecked, issue, head_sha=HEAD, base_sha=BASE, promotion=False)
    with pytest.raises(preflight.PreflightError, match="complete evidence"):
        preflight.validate_pr_body(placeholder, issue, head_sha=HEAD, base_sha=BASE, promotion=False)

    missing_item = body.replace("- [x] `mypy`\n", "", 1)
    with pytest.raises(preflight.PreflightError, match="complete canonical Verification checklist"):
        preflight.validate_pr_body(missing_item, issue, head_sha=HEAD, base_sha=BASE, promotion=False)

    negative = body.replace(
        "<!-- hunter-verification:v1 ruff=pass black=pass mypy=pass pytest=pass -->",
        "No commands were run.",
    )
    with pytest.raises(preflight.PreflightError, match="structured result marker"):
        preflight.validate_pr_body(negative, issue, head_sha=HEAD, base_sha=BASE, promotion=True)


def test_pr_body_rejects_missing_matrix() -> None:
    issue = fixture_issue()
    body = generated_body().replace("## Acceptance-criteria matrix", "## Acceptance evidence")

    with pytest.raises(preflight.PreflightError, match="canonical section"):
        preflight.validate_pr_body(body, issue, head_sha=HEAD, base_sha=BASE, promotion=False)


def test_pr_body_rejects_missing_canonical_non_matrix_section() -> None:
    issue = fixture_issue()
    body = generated_body().replace("## Operational validation", "## Runtime notes")

    with pytest.raises(preflight.PreflightError, match="Operational validation"):
        preflight.validate_pr_body(body, issue, head_sha=HEAD, base_sha=BASE, promotion=False)


def test_pr_body_rejects_duplicate_canonical_section() -> None:
    issue = fixture_issue()
    body = generated_body() + "\n## Verification\n\nduplicate\n"

    with pytest.raises(preflight.PreflightError, match="exactly one canonical section"):
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


def test_external_command_timeout_is_bounded_and_status_safe(monkeypatch) -> None:
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=("gh", "api"), timeout=preflight.COMMAND_TIMEOUT_SECONDS)

    monkeypatch.setattr(preflight.subprocess, "run", timeout)

    with pytest.raises(preflight.PreflightError, match=r"timed out after 30s: gh") as exc_info:
        preflight._run(("gh", "api", "repos/fafa33/Project-Hunter"))

    assert "token" not in str(exc_info.value).lower()


def test_external_command_failure_does_not_embed_raw_diagnostics_in_public_error(monkeypatch, capsys) -> None:
    completed = SimpleNamespace(
        returncode=1,
        stdout="",
        stderr="Authorization: Bearer super-secret-token",
    )
    monkeypatch.setattr(preflight.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(preflight.PreflightError) as exc_info:
        preflight._run(("gh", "api", "repos/fafa33/Project-Hunter"))

    assert "super-secret-token" not in str(exc_info.value)
    assert str(exc_info.value) == "external command failed (1): gh"
    assert "super-secret-token" in capsys.readouterr().err


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


def test_rule_21_rejects_closed_issue(monkeypatch) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["state"] = "closed"
    monkeypatch.setattr(preflight, "_gh_json", lambda _args: payload)

    with pytest.raises(preflight.PreflightError, match="not open"):
        preflight.load_issue("fafa33/Project-Hunter", 276)


def test_production_cli_rejects_caller_supplied_issue_identity() -> None:
    with pytest.raises(SystemExit):
        preflight._parser().parse_args(
            [
                "branch",
                "--repo",
                "fafa33/Project-Hunter",
                "--issue",
                "276",
                "--objective",
                "Governance enforcement: mandatory agent preflight and PR generator",
                "--issue-json",
                str(FIXTURE),
            ]
        )


@pytest.mark.parametrize("action", ["pr-create", "pr-update"])
def test_pr_mutation_actions_require_canonical_title_and_body(action: str) -> None:
    argv = [
        action,
        "--repo",
        "fafa33/Project-Hunter",
        "--issue",
        "276",
        "--objective",
        "Governance enforcement: mandatory agent preflight and PR generator",
    ]
    if action == "pr-update":
        argv.extend(("--pr", "277"))
    with pytest.raises(SystemExit):
        preflight._parser().parse_args(argv)


def test_pr_create_requires_canonical_target_base_branch() -> None:
    with pytest.raises(SystemExit):
        preflight._parser().parse_args(
            [
                "pr-create",
                "--repo",
                "fafa33/Project-Hunter",
                "--issue",
                "276",
                "--objective",
                "Governance enforcement: mandatory agent preflight and PR generator",
                "--pr-title",
                "Governance enforcement: mandatory agent preflight and PR generator",
                "--pr-body-file",
                "body.md",
            ]
        )


def test_post_commit_actions_reject_caller_git_identity_overrides() -> None:
    for action in ("push", "pr-create", "pr-update"):
        with pytest.raises(SystemExit):
            preflight._parser().parse_args(
                [
                    action,
                    "--repo",
                    "fafa33/Project-Hunter",
                    "--issue",
                    "276",
                    "--objective",
                    "Governance enforcement: mandatory agent preflight and PR generator",
                    "--branch",
                    "governance/issue-276-spoofed",
                    "--commit-message",
                    "fix: spoofed #276",
                ]
            )


def test_pr_update_binds_authorization_to_live_target_pr_metadata(tmp_path, monkeypatch) -> None:
    issue = fixture_issue()
    body_file = tmp_path / "body.md"
    body_file.write_text(generated_body(), encoding="utf-8")
    monkeypatch.setattr(preflight, "validate_canonical_governance", lambda _root: None)
    monkeypatch.setattr(preflight, "load_issue", lambda _repo, _number: issue)
    monkeypatch.setattr(preflight, "_git_branch", lambda _root: "governance/issue-276-agent-preflight")
    monkeypatch.setattr(preflight, "_git_commit_message", lambda _root: "fix: govern update #276")
    monkeypatch.setattr(preflight, "_git_exact_pair", lambda _root, _base: (HEAD, BASE))
    monkeypatch.setattr(
        preflight,
        "_gh_json",
        lambda _args: {
            "title": issue.title,
            "body": generated_body(),
            "head": {"ref": "governance/issue-999-unrelated"},
        },
    )
    args = SimpleNamespace(
        action="pr-update",
        repo_root=str(ROOT),
        repo=issue.repository,
        issue=issue.number,
        objective=issue.title,
        branch="governance/issue-276-agent-preflight",
        commit_message="fix: govern update #276",
        pr_title=issue.title,
        pr_body_file=str(body_file),
        pr=999,
        base_ref="origin/main",
        allow_governance_diff_check=False,
    )

    with pytest.raises(preflight.PreflightError, match="target PR head branch"):
        preflight._cmd_identity_action(args)


def test_generator_trace_is_derived_from_git_and_head_sha_override_is_rejected() -> None:
    issue = fixture_issue()
    with patch.object(preflight, "_git_exact_pair", return_value=(HEAD, BASE)) as exact_pair:
        body = preflight.generate_pr_body(
            issue,
            repo_root=ROOT,
            base_ref="origin/main",
            template_text=canonical_template(),
            changed_files=(),
        )
    assert preflight.trace_identity(body) == (276, HEAD, BASE)
    exact_pair.assert_called_once_with(ROOT, "origin/main")
    with pytest.raises(SystemExit):
        preflight._parser().parse_args(
            [
                "generate-pr-body",
                "--repo",
                "fafa33/Project-Hunter",
                "--issue",
                "276",
                "--objective",
                issue.title,
                "--head-sha",
                "c" * 40,
            ]
        )


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


def test_ownership_guard_inspects_non_owner_governance_documents() -> None:
    with pytest.raises(preflight.PreflightError, match="outside canonical owner"):
        preflight.validate_ownership_added_lines(
            {"docs/COMPETING_GOVERNANCE.md": ["All changes are ready for review without independent review."]}
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


def _live_review_evidence(*, author: str, body: str, suffix: str) -> preflight.LiveReviewEvidence:
    return preflight.LiveReviewEvidence(
        url=f"https://github.com/fafa33/Project-Hunter/pull/277#{suffix}",
        author=author,
        body=body,
        pull_request_number=277,
        kind="review_comment" if suffix.startswith("discussion_r") else "issue_comment",
        review_state="changes_requested" if suffix.startswith("discussion_r") else "",
        commit_id=HEAD if suffix.startswith("discussion_r") else "",
    )


def test_finding_resolution_is_bound_to_live_independent_exact_pair_evidence() -> None:
    finding = _live_review_evidence(
        author="independent-reviewer",
        suffix="discussion_r1",
        body=(
            f"<!-- hunter-review-finding:v1 id=F-004 severity=blocking classification=systemic "
            f"head={HEAD} base={BASE} -->\n"
            "Classification evidence: Caller-controlled classification can recur across every finding.\n"
            "Reusable boundary: live review finding resolver"
        ),
    )
    verifier = _live_review_evidence(
        author="independent-verifier",
        suffix="issuecomment-2",
        body=(
            f"<!-- hunter-review-verification:v1 finding=F-004 head={HEAD} base={BASE} outcome=resolved -->\n"
            "Verification evidence: Independent counterexample now fails.\n"
            "Durable guard evidence: Counterfactual regression covers the reusable resolver boundary."
        ),
    )

    resolution = preflight.finding_resolution_from_live_review(
        finding_comment=finding,
        verifier_comment=verifier,
        pr_author="implementer",
        head_sha=HEAD,
        base_sha=BASE,
    )

    assert resolution.classification == "systemic"
    assert resolution.severity == "blocking"
    with pytest.raises(preflight.PreflightError, match="independent"):
        preflight.finding_resolution_from_live_review(
            finding_comment=preflight.LiveReviewEvidence(
                finding.url, "implementer", finding.body, 277, "review_comment", "changes_requested", HEAD
            ),
            verifier_comment=verifier,
            pr_author="implementer",
            head_sha=HEAD,
            base_sha=BASE,
        )
    with pytest.raises(preflight.PreflightError, match="stale"):
        preflight.finding_resolution_from_live_review(
            finding_comment=finding,
            verifier_comment=verifier,
            pr_author="implementer",
            head_sha="c" * 40,
            base_sha=BASE,
        )

    with pytest.raises(preflight.PreflightError, match="distinct independent authors"):
        preflight.finding_resolution_from_live_review(
            finding_comment=finding,
            verifier_comment=preflight.LiveReviewEvidence(
                verifier.url, finding.author, verifier.body, 277, "issue_comment", "", ""
            ),
            pr_author="implementer",
            head_sha=HEAD,
            base_sha=BASE,
        )
    marker_only = preflight.LiveReviewEvidence(
        verifier.url,
        verifier.author,
        f"<!-- hunter-review-verification:v1 finding=F-004 head={HEAD} base={BASE} outcome=resolved -->",
    )
    with pytest.raises(preflight.PreflightError, match="Verification evidence"):
        preflight.finding_resolution_from_live_review(
            finding_comment=finding,
            verifier_comment=marker_only,
            pr_author="implementer",
            head_sha=HEAD,
            base_sha=BASE,
        )


def test_hostile_review_requires_independent_exact_pair_positive_evidence(monkeypatch) -> None:
    review = {
        "id": 9,
        "user": {"login": "independent-reviewer"},
        "commit_id": HEAD,
        "state": "COMMENTED",
        "submitted_at": "2026-08-16T20:00:00Z",
        "body": (
            f"<!-- hunter-hostile-review:v1 head={HEAD} base={BASE} outcome=no-blocking-findings -->\n"
            "Hostile review evidence: Reproduced all required counterexamples against the exact pair.\n"
            "Scope probed: All original blocker classes and newly changed enforcement boundaries.\n"
            "Limitations: GitHub branch protection remains external human authority.\n"
            "Unresolved blocking findings: 0"
        ),
    }
    monkeypatch.setattr(preflight, "_gh_json", lambda _args: [review])
    preflight.require_independent_hostile_review(
        repository="fafa33/Project-Hunter",
        pr_number=277,
        pr_author="implementer",
        head_sha=HEAD,
        base_sha=BASE,
    )

    review["user"] = {"login": "implementer"}
    with pytest.raises(preflight.PreflightError, match="lacks independent"):
        preflight.require_independent_hostile_review(
            repository="fafa33/Project-Hunter",
            pr_number=277,
            pr_author="implementer",
            head_sha=HEAD,
            base_sha=BASE,
        )


def test_hostile_review_rejects_dismissed_marker_only_and_reads_later_pages(monkeypatch) -> None:
    dismissed = {
        "id": 1,
        "user": {"login": "independent-reviewer"},
        "commit_id": HEAD,
        "state": "DISMISSED",
        "submitted_at": "2026-08-16T19:00:00Z",
        "body": f"<!-- hunter-hostile-review:v1 head={HEAD} base={BASE} outcome=no-blocking-findings -->",
    }
    adverse = {
        "id": 101,
        "user": {"login": "independent-reviewer"},
        "commit_id": HEAD,
        "state": "CHANGES_REQUESTED",
        "submitted_at": "2026-08-16T21:00:00Z",
        "body": (
            f"<!-- hunter-hostile-review:v1 head={HEAD} base={BASE} outcome=changes-required -->\n"
            "Hostile review evidence: A later page contains a reproducible authorization bypass.\n"
            "Scope probed: Complete hostile-review evidence resolver across pagination.\n"
            "Limitations: No repository mutation was attempted.\n"
            "Unresolved blocking findings: 1"
        ),
    }
    first_page = [dismissed, *({"id": index} for index in range(2, 101))]
    monkeypatch.setattr(preflight, "_gh_json", lambda args: [adverse] if "page=2" in args[0] else first_page)

    with pytest.raises(preflight.PreflightError, match="CHANGES-REQUIRED"):
        preflight.require_independent_hostile_review(
            repository="fafa33/Project-Hunter",
            pr_number=277,
            pr_author="implementer",
            head_sha=HEAD,
            base_sha=BASE,
        )


def test_newer_malformed_adverse_hostile_review_invalidates_older_pass(monkeypatch) -> None:
    older_pass = {
        "id": 10,
        "user": {"login": "independent-reviewer"},
        "commit_id": HEAD,
        "state": "COMMENTED",
        "submitted_at": "2026-08-16T20:00:00Z",
        "body": (
            f"<!-- hunter-hostile-review:v1 head={HEAD} base={BASE} outcome=no-blocking-findings -->\n"
            "Hostile review evidence: Required counterexamples fail closed.\n"
            "Scope probed: All governed mutation and readiness boundaries.\n"
            "Limitations: External human approval remains separate.\n"
            "Unresolved blocking findings: 0"
        ),
    }
    newer_malformed = {
        "id": 11,
        "user": {"login": "independent-reviewer"},
        "commit_id": HEAD,
        "state": "CHANGES_REQUESTED",
        "submitted_at": "2026-08-16T21:00:00Z",
        "body": (
            f"<!-- hunter-hostile-review:v1 head={HEAD} base={BASE} outcome=changes-required -->\n"
            "Unresolved blocking findings: 1"
        ),
    }
    monkeypatch.setattr(preflight, "_gh_json", lambda _args: [older_pass, newer_malformed])

    with pytest.raises(preflight.PreflightError, match="report is incomplete"):
        preflight.require_independent_hostile_review(
            repository="fafa33/Project-Hunter",
            pr_number=277,
            pr_author="implementer",
            head_sha=HEAD,
            base_sha=BASE,
        )


def test_live_ready_path_composes_hostile_review_authority(monkeypatch) -> None:
    issue = fixture_issue()
    body = generated_body()
    pr = {
        "draft": False,
        "title": issue.title,
        "body": body,
        "user": {"login": "implementer"},
        "head": {"ref": "governance/issue-276-agent-preflight"},
    }
    monkeypatch.setattr(preflight, "validate_canonical_governance", lambda _root: None)
    monkeypatch.setattr(preflight, "_gh_json", lambda _args: pr)
    monkeypatch.setattr(preflight, "load_issue", lambda _repo, _number: issue)
    monkeypatch.setattr(preflight, "_gh_pr_oids", lambda _repo, _pr: (HEAD, BASE))
    monkeypatch.setattr(preflight, "_run", lambda _args: "")
    monkeypatch.setattr(
        preflight,
        "require_independent_hostile_review",
        lambda **_kwargs: (_ for _ in ()).throw(preflight.PreflightError("hostile evidence missing")),
    )

    with pytest.raises(preflight.PreflightError, match="hostile evidence missing"):
        preflight._cmd_live_pr(SimpleNamespace(repo_root=str(ROOT), repo=issue.repository, pr=277))


def test_live_ready_rejects_missing_positive_hostile_review(monkeypatch) -> None:
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
        unresolved_thread_ids=(),
        changes_requested=(),
        unacknowledged_comments=(),
    )

    import hunter_merge_readiness as readiness

    monkeypatch.setattr(readiness, "init_globals", lambda: None)
    monkeypatch.setattr(readiness, "read_current_state", lambda _pr: state)
    monkeypatch.setattr(readiness, "feedback_error", lambda _state: None)

    def gh_json(args):
        if "/reviews?" in args[0]:
            return []
        return {
            "user": {"login": "implementer"},
            "head": {"ref": "governance/issue-276-agent-preflight"},
        }

    monkeypatch.setattr(preflight, "_gh_json", gh_json)
    readiness.repo = issue.repository
    readiness.repo_owner = "fafa33"
    readiness.token = "token"

    with pytest.raises(preflight.PreflightError, match="lacks independent exact-pair hostile-review"):
        preflight._require_current_state_ready(277, issue)


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
