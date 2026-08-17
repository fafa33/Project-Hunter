import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure scripts directory is in PYTHONPATH so we can import hunter_draft_promotion_signal
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import hunter_draft_promotion_signal


def _governing_issue() -> hunter_draft_promotion_signal.governance_preflight.IssueIdentity:
    return hunter_draft_promotion_signal.governance_preflight.IssueIdentity(
        repository="fafa33/Project-Hunter",
        number=276,
        title="Governance enforcement: mandatory agent preflight and PR generator",
        body="",
        state="open",
    )


class MockGitHubServer:
    def __init__(self):
        self.pulls = {}
        self.check_runs = {}
        self.statuses = {}
        self.comments = {}
        self.published = []
        self.patched_bodies = {}
        self.pull_files = {}

    def request_json(self, method, path, payload=None):
        clean_path = path.split("?")[0]

        if method == "POST":
            if clean_path.startswith("statuses/"):
                sha = clean_path.split("/")[-1]
                self.published.append((sha, payload["state"], payload["description"]))
                return {}
            if clean_path.startswith("issues/") and clean_path.endswith("/comments"):
                pr_num = int(clean_path.split("/")[1])
                self.comments.setdefault(pr_num, []).append(
                    {"id": len(self.comments.get(pr_num, [])) + 1, "body": payload["body"]}
                )
                return {}
            return {}

        if method == "PATCH":
            if clean_path.startswith("pulls/"):
                pr_num = int(clean_path.split("/")[1])
                self.patched_bodies[pr_num] = payload["body"]
                if pr_num in self.pulls:
                    self.pulls[pr_num]["body"] = payload["body"]
                return {}
            if clean_path.startswith("issues/comments/"):
                comment_id = int(clean_path.split("/")[-1])
                for comments in self.comments.values():
                    for comment in comments:
                        if int(comment["id"]) == comment_id:
                            comment["body"] = payload["body"]
                            return {}

        if clean_path.startswith("pulls/") and clean_path.endswith("/files"):
            pr_num = int(clean_path.split("/")[1])
            return self.pull_files.get(pr_num, [])

        if clean_path.startswith("pulls/"):
            pr_num = int(clean_path.split("/")[1])
            return self.pulls.get(pr_num, {})

        if clean_path.startswith("commits/"):
            parts = clean_path.split("/")
            sha = parts[1]
            sub_resource = parts[2]
            if sub_resource == "check-runs":
                return {"check_runs": self.check_runs.get(sha, [])}
            if sub_resource == "status":
                return {"statuses": self.statuses.get(sha, [])}
            if sub_resource == "statuses":
                return self.statuses.get(sha, [])

        if clean_path.startswith("issues/") and clean_path.endswith("/comments"):
            pr_num = int(clean_path.split("/")[1])
            return self.comments.get(pr_num, [])

        if clean_path == "pulls":
            return list(self.pulls.values())

        return {}


@pytest.fixture
def gh():
    server = MockGitHubServer()
    hunter_draft_promotion_signal.repo = "fafa33/Project-Hunter"
    hunter_draft_promotion_signal.token = "fake-token"
    hunter_draft_promotion_signal.run_url = "https://github.com/fafa33/Project-Hunter/actions/runs/1"

    with (
        patch("hunter_draft_promotion_signal.request_json", side_effect=server.request_json),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "request_json",
            side_effect=server.request_json,
        ),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "unresolved_review_thread_ids",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "current_changes_requested_reviewers",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "unacknowledged_top_level_comments",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "require_independent_hostile_review",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "base_head_oids",
            return_value=("b" * 40, "b" * 40),
        ),
    ):
        yield server


def green_pr(number, sha, body, draft=True, state="open", base="main"):
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "base": {"ref": base, "sha": "b" * 40},
        "head": {"sha": sha},
        "user": {"login": "implementer"},
        "body": body,
    }


def green_checks(gh, sha):
    gh.check_runs[sha] = [
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 1},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 2},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 3},
    ]
    pr = next(pr for pr in gh.pulls.values() if (pr.get("head") or {}).get("sha") == sha)
    inputs = hunter_draft_promotion_signal.merge_readiness.GovernanceInputs(
        pull_request_number=int(pr["number"]),
        head_sha="b" * 40,
        base_sha="b" * 40,
        base_ref=str((pr.get("base") or {}).get("ref") or "").strip(),
        title=str(pr.get("title") or ""),
        body=str(pr.get("body") or ""),
        draft=bool(pr.get("draft")),
        conflicting=hunter_draft_promotion_signal.merge_readiness.conflicting_from_rest(pr.get("mergeable")),
        changed_paths=(),
    )
    revision = hunter_draft_promotion_signal.merge_readiness.governance_revision(inputs)
    gh.statuses[sha] = [
        {
            "context": "Hunter Governance Review",
            "state": "success",
            "id": 1,
            "description": f"[hgr:{int(pr['number'])}:{revision}] Approved for head {sha}",
        }
    ]


def test_single_declaration_line_does_not_crash():
    body = "## Implementer readiness declaration\n\n- [x] `READY FOR REVIEW` — all good.\n"
    matches, checked_label = hunter_draft_promotion_signal.parse_readiness_declaration(body)
    assert len(matches) == 1
    assert checked_label == "READY FOR REVIEW"


def test_all_three_lines_one_checked_still_works():
    body = "- [ ] `READY FOR REVIEW` — a\n" "- [x] `CHANGES REQUIRED` — b\n" "- [ ] `BLOCKED` — c\n"
    matches, checked_label = hunter_draft_promotion_signal.parse_readiness_declaration(body)
    assert len(matches) == 3
    assert checked_label == "CHANGES REQUIRED"


def test_no_declaration_lines_fails_closed():
    with pytest.raises(RuntimeError, match="No implementer readiness declaration found"):
        hunter_draft_promotion_signal.parse_readiness_declaration("Just a description, no checkboxes.")


def test_zero_checked_fails_closed():
    body = "- [ ] `READY FOR REVIEW` — a\n- [ ] `CHANGES REQUIRED` — b\n"
    with pytest.raises(RuntimeError, match="missing, ambiguous, or unparseable"):
        hunter_draft_promotion_signal.parse_readiness_declaration(body)


def test_multiple_checked_fails_closed():
    body = "- [x] `READY FOR REVIEW` — a\n- [x] `BLOCKED` — b\n"
    with pytest.raises(RuntimeError, match="missing, ambiguous, or unparseable"):
        hunter_draft_promotion_signal.parse_readiness_declaration(body)


def test_duplicated_label_fails_closed():
    body = "- [x] `READY FOR REVIEW` — a\n- [ ] `READY FOR REVIEW` — duplicate\n"
    with pytest.raises(RuntimeError, match="duplicated label"):
        hunter_draft_promotion_signal.parse_readiness_declaration(body)


def test_synchronize_single_line_ready_is_a_noop(gh):
    body = "- [x] `READY FOR REVIEW` — all good.\n"
    hunter_draft_promotion_signal.synchronize_ready_metadata(123, body)
    assert 123 not in gh.patched_bodies


def test_synchronize_rewrites_to_ready_for_review(gh):
    body = "- [ ] `READY FOR REVIEW` — a\n- [x] `CHANGES REQUIRED` — b\n- [ ] `BLOCKED` — c\n"
    hunter_draft_promotion_signal.synchronize_ready_metadata(123, body)
    assert 123 in gh.patched_bodies
    assert "[x] `READY FOR REVIEW`" in gh.patched_bodies[123]
    assert "[ ] `CHANGES REQUIRED`" in gh.patched_bodies[123]


def test_synchronize_without_ready_label_fails_closed_and_does_not_patch(gh):
    body = "- [x] `CHANGES REQUIRED` — more work remains.\n"
    with pytest.raises(RuntimeError, match="READY FOR REVIEW declaration is missing"):
        hunter_draft_promotion_signal.synchronize_ready_metadata(123, body)
    assert 123 not in gh.patched_bodies


def test_synchronize_ambiguous_body_raises_and_does_not_patch(gh):
    body = "- [ ] `READY FOR REVIEW` — a\n- [ ] `CHANGES REQUIRED` — b\n"
    with pytest.raises(RuntimeError):
        hunter_draft_promotion_signal.synchronize_ready_metadata(123, body)
    assert 123 not in gh.patched_bodies


def test_evaluate_with_single_declaration_line_reaches_success_and_comments(gh):
    sha = "sha_123"
    body = "## Implementer readiness declaration\n\n- [x] `READY FOR REVIEW` — all good.\n"
    gh.pulls[123] = green_pr(123, sha, body)
    green_checks(gh, sha)

    with (
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "load_issue",
            return_value=_governing_issue(),
        ),
        patch.object(
            hunter_draft_promotion_signal,
            "_governing_issue_number",
            return_value=276,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_pr_body",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "blocked_acceptance_criteria",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_ready_evidence",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_trace_against_state",
            return_value=None,
        ),
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[123])

    assert gh.published[-1][1] == "success"
    assert "Ready to promote from Draft" in gh.published[-1][2]
    assert len(gh.comments.get(123, [])) == 1
    assert "Hunter Draft Promotion" in gh.comments[123][0]["body"]


def test_draft_promotion_requires_positive_hostile_review_before_mutation(gh):
    sha = "sha_hostile_missing"
    body = "- [ ] `READY FOR REVIEW`\n- [x] `CHANGES REQUIRED`\n- [ ] `BLOCKED`\n"
    gh.pulls[124] = green_pr(124, sha, body)
    green_checks(gh, sha)

    with (
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "load_issue",
            return_value=_governing_issue(),
        ),
        patch.object(
            hunter_draft_promotion_signal,
            "_governing_issue_number",
            return_value=276,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_pr_body",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "blocked_acceptance_criteria",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_trace_against_state",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_ready_evidence",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "require_independent_hostile_review",
            side_effect=hunter_draft_promotion_signal.governance_preflight.PreflightError("hostile evidence missing"),
        ),
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[124])

    assert 124 not in gh.patched_bodies
    assert not any(state == "success" for _sha, state, _description in gh.published)
    assert gh.published[-1][1] == "pending"
    assert "hostile evidence missing" in gh.published[-1][2]


def test_draft_promotion_retracts_success_for_invalid_ready_evidence(gh):
    sha = "sha_negative_evidence"
    body = "- [ ] `READY FOR REVIEW`\n- [x] `CHANGES REQUIRED`\n- [ ] `BLOCKED`\nNo commands were run.\n"
    gh.pulls[125] = green_pr(125, sha, body)
    green_checks(gh, sha)

    with (
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "load_issue",
            return_value=_governing_issue(),
        ),
        patch.object(
            hunter_draft_promotion_signal,
            "_governing_issue_number",
            return_value=276,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_pr_body",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "blocked_acceptance_criteria",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_ready_evidence",
            side_effect=hunter_draft_promotion_signal.governance_preflight.PreflightError(
                "structured result marker missing"
            ),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_trace_against_state",
            return_value=None,
        ),
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[125])

    assert 125 not in gh.patched_bodies
    assert gh.published[-1][1] == "pending"
    assert "structured result marker missing" in gh.published[-1][2]


def test_draft_promotion_rejects_superseded_head_marker(gh):
    sha = "sha_current_head"
    stale_sha = "sha_old_head_123"
    body = (
        f"<!-- hunter-governance-preflight:v1 issue=276 head={stale_sha} base={'b' * 40} -->\n"
        "- [ ] `READY FOR REVIEW`\n- [x] `CHANGES REQUIRED`\n- [ ] `BLOCKED`\n"
    )
    gh.pulls[127] = green_pr(127, sha, body)
    green_checks(gh, sha)

    with (
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "load_issue",
            return_value=_governing_issue(),
        ),
        patch.object(
            hunter_draft_promotion_signal,
            "_governing_issue_number",
            return_value=276,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_pr_body",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "blocked_acceptance_criteria",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_trace_against_state",
            return_value="PR body evidence is stale relative to the current source head.",
        ),
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[127])

    assert 127 not in gh.patched_bodies
    assert gh.published[-1][1] == "pending"
    assert "stale relative to the current source head" in gh.published[-1][2]


def test_draft_promotion_retracts_stale_ready_body_and_success_comment(gh):
    sha = "sha_stale_success"
    body = "- [x] `READY FOR REVIEW`\n- [ ] `CHANGES REQUIRED`\n- [ ] `BLOCKED`\n"
    gh.pulls[126] = green_pr(126, sha, body)
    green_checks(gh, sha)
    gh.comments[126] = [
        {
            "id": 77,
            "body": f"<!-- hunter-draft-promotion:{sha} -->\n✅ **Hunter Draft Promotion:** stale success",
        }
    ]

    with (
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "load_issue",
            return_value=_governing_issue(),
        ),
        patch.object(
            hunter_draft_promotion_signal,
            "_governing_issue_number",
            return_value=276,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_pr_body",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "blocked_acceptance_criteria",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_ready_evidence",
            side_effect=hunter_draft_promotion_signal.governance_preflight.PreflightError(
                "structured result marker missing"
            ),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_trace_against_state",
            return_value=None,
        ),
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[126])

    assert "- [ ] `READY FOR REVIEW`" in gh.patched_bodies[126]
    assert "- [x] `CHANGES REQUIRED`" in gh.patched_bodies[126]
    assert "invalidated" in gh.comments[126][0]["body"]
    assert "stale success" not in gh.comments[126][0]["body"]
    assert gh.published[-1][1] == "pending"


def test_closed_draft_pr_is_outside_promotion_scope(gh):
    sha = "sha_closed"
    body = "- [x] `READY FOR REVIEW` — stale.\n"
    gh.pulls[280] = green_pr(280, sha, body, state="closed")
    green_checks(gh, sha)

    hunter_draft_promotion_signal.evaluate({"number": 280, "draft": True})

    assert gh.published == []
    assert 280 not in gh.patched_bodies
    assert not gh.comments.get(280)


def test_non_main_draft_pr_is_outside_promotion_scope(gh):
    sha = "sha_other_base"
    body = "- [x] `READY FOR REVIEW` — stale.\n"
    gh.pulls[281] = green_pr(281, sha, body, base="release")
    green_checks(gh, sha)

    hunter_draft_promotion_signal.evaluate({"number": 281, "draft": True})

    assert gh.published == []
    assert 281 not in gh.patched_bodies
    assert not gh.comments.get(281)


def test_live_scope_is_reread_instead_of_trusting_event_payload(gh):
    sha = "sha_live_scope"
    body = "- [x] `READY FOR REVIEW` — valid.\n"
    gh.pulls[282] = green_pr(282, sha, body)
    green_checks(gh, sha)

    with (
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "load_issue",
            return_value=_governing_issue(),
        ),
        patch.object(
            hunter_draft_promotion_signal,
            "_governing_issue_number",
            return_value=276,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_pr_body",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "blocked_acceptance_criteria",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_ready_evidence",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_trace_against_state",
            return_value=None,
        ),
    ):
        hunter_draft_promotion_signal.evaluate(
            {"number": 282, "state": "closed", "draft": False, "base": {"ref": "release"}}
        )

    assert gh.published[-1][1] == "success"


def test_evaluate_with_ambiguous_declaration_does_not_silently_disappear(gh):
    sha = "sha_123"
    body = "- [ ] `READY FOR REVIEW` — a\n- [ ] `CHANGES REQUIRED` — b\n"
    gh.pulls[123] = green_pr(123, sha, body)
    green_checks(gh, sha)

    hunter_draft_promotion_signal.evaluate(gh.pulls[123])

    assert not any(state == "success" for _sha, state, _desc in gh.published)
    assert gh.published[-1][1] == "pending"
    assert "Closes/Fixes governing Issue identity" in gh.published[-1][2]
    assert 123 not in gh.patched_bodies


def test_evaluate_rejects_negative_pass_evidence_via_shared_boundary(gh, capsys):
    sha = "sha_negative_pass_evidence"
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "governance" / "issue_276_preflight.json").read_text(encoding="utf-8")
    )
    issue = hunter_draft_promotion_signal.governance_preflight.IssueIdentity.from_payload(
        "fafa33/Project-Hunter", fixture
    )
    base_sha = "b" * 40
    evidence_url = "https://github.com/fafa33/Project-Hunter/pull/277"

    def evidence_pair(kind: str, result: str) -> str:
        return (
            f"<!-- hunter-evidence:v1 kind={kind} result={result} reference={evidence_url} "
            f"head={base_sha} base={base_sha} -->"
        )

    with patch.object(
        hunter_draft_promotion_signal.governance_preflight,
        "_git_exact_pair",
        return_value=(base_sha, base_sha),
    ):
        body = hunter_draft_promotion_signal.governance_preflight.generate_pr_body(
            issue,
            repo_root=Path(__file__).parent.parent,
            base_ref="main",
            template_text=(Path(__file__).parent.parent / ".github" / "pull_request_template.md").read_text(
                encoding="utf-8"
            ),
            changed_files=("scripts/hunter_governance_preflight.py",),
            evidence={
                criterion: {"status": "PASS", "evidence": f"deterministic proof {index}"}
                for index, criterion in enumerate(
                    hunter_draft_promotion_signal.governance_preflight.issue_acceptance_criteria(issue.body),
                    start=1,
                )
            },
            verification=(evidence_pair("verification", "verified"),),
            operational_evidence=(evidence_pair("operational", "not-applicable"),),
        )
    body = body.replace("- [ ] `READY FOR REVIEW`", "- [x] `READY FOR REVIEW`", 1)
    body = body.replace("- [x] `CHANGES REQUIRED`", "- [ ] `CHANGES REQUIRED`", 1)
    body = body.replace(
        "## Verification\n\n",
        "## Verification\n\n<!-- hunter-verification:v1 ruff=pass black=pass mypy=pass pytest=pass -->\n\n",
        1,
    )
    body = body.replace(
        "## Operational validation\n\n",
        "## Operational validation\n\n<!-- hunter-operational:v1 runbook=executed -->\n\n",
        1,
    )
    first_row = hunter_draft_promotion_signal.governance_preflight.parse_acceptance_matrix(body)[0]
    body = body.replace(
        f"| {first_row.criterion} | PASS | {first_row.evidence} |",
        f"| {first_row.criterion} | PASS | tests failed |",
        1,
    )

    gh.pulls[277] = green_pr(277, sha, body)
    green_checks(gh, sha)

    with patch.object(
        hunter_draft_promotion_signal.governance_preflight,
        "load_issue",
        return_value=issue,
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[277])

    assert not any(state == "success" for _sha, state, _description in gh.published)
    assert gh.published[-1][1] == "pending"
    assert "Acceptance criterion" in gh.published[-1][2]
    assert "placeholder or explicitly negative evidence" in capsys.readouterr().out
    assert not gh.comments.get(277)
    assert "- [ ] `READY FOR REVIEW`" in gh.patched_bodies[277]
    assert "- [x] `CHANGES REQUIRED`" in gh.patched_bodies[277]


@pytest.mark.parametrize("checked", ["CHANGES REQUIRED", "READY FOR REVIEW"])
def test_draft_promotion_fails_closed_on_fail_criterion_via_real_path(gh, checked):
    """A FAIL matrix row must never yield a success signal, a READY sync, or a
    positive promotion comment, regardless of the checked declaration.

    This runs the real validation path (no mocked validate_pr_body or
    validate_ready_evidence): the promotion=False validation alone permitted
    FAIL/BLOCKED rows, so the advisory layer published false success and
    synchronized READY FOR REVIEW while a criterion was still failing.
    """
    sha = "sha_fail_criterion"
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "governance" / "issue_276_preflight.json").read_text(encoding="utf-8")
    )
    issue = hunter_draft_promotion_signal.governance_preflight.IssueIdentity.from_payload(
        "fafa33/Project-Hunter", fixture
    )
    base_sha = "b" * 40
    evidence_url = "https://github.com/fafa33/Project-Hunter/pull/277"

    def evidence_pair(kind: str, result: str) -> str:
        return (
            f"<!-- hunter-evidence:v1 kind={kind} result={result} reference={evidence_url} "
            f"head={base_sha} base={base_sha} -->"
        )

    with patch.object(
        hunter_draft_promotion_signal.governance_preflight,
        "_git_exact_pair",
        return_value=(base_sha, base_sha),
    ):
        body = hunter_draft_promotion_signal.governance_preflight.generate_pr_body(
            issue,
            repo_root=Path(__file__).parent.parent,
            base_ref="main",
            template_text=(Path(__file__).parent.parent / ".github" / "pull_request_template.md").read_text(
                encoding="utf-8"
            ),
            changed_files=("scripts/hunter_governance_preflight.py",),
            evidence={
                criterion: {"status": "PASS", "evidence": f"deterministic proof {index}"}
                for index, criterion in enumerate(
                    hunter_draft_promotion_signal.governance_preflight.issue_acceptance_criteria(issue.body),
                    start=1,
                )
            },
            verification=(evidence_pair("verification", "verified"),),
            operational_evidence=(evidence_pair("operational", "not-applicable"),),
        )
    if checked == "READY FOR REVIEW":
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
    first_row = hunter_draft_promotion_signal.governance_preflight.parse_acceptance_matrix(body)[0]
    body = body.replace(
        f"| {first_row.criterion} | PASS | {first_row.evidence} |",
        f"| {first_row.criterion} | FAIL | {first_row.evidence} |",
        1,
    )

    gh.pulls[278] = green_pr(278, sha, body)
    green_checks(gh, sha)
    # A stale success comment from a prior sweep must be retracted, never
    # re-affirmed, while the criterion remains FAIL.
    gh.comments[278] = [
        {
            "id": 778,
            "body": f"<!-- hunter-draft-promotion:{sha} -->\n✅ **Hunter Draft Promotion:** stale success",
        }
    ]

    with patch.object(
        hunter_draft_promotion_signal.governance_preflight,
        "load_issue",
        return_value=issue,
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[278])

    assert not any(state == "success" for _sha, state, _description in gh.published)
    assert gh.published[-1][1] == "pending"
    assert gh.published[-1][2].startswith(
        "Waiting for Draft promotion prerequisites: " "Ready-for-review promotion is blocked by FAIL/BLOCKED criteria: "
    )
    assert first_row.criterion[:20] in gh.published[-1][2]
    assert "invalidated" in gh.comments[278][0]["body"]
    assert "stale success" not in gh.comments[278][0]["body"]
    if checked == "CHANGES REQUIRED":
        assert 278 not in gh.patched_bodies
    else:
        assert "- [ ] `READY FOR REVIEW`" in gh.patched_bodies[278]
        assert "- [x] `CHANGES REQUIRED`" in gh.patched_bodies[278]


def test_unresolved_review_thread_blocks_promotion_and_metadata_mutation(gh):
    sha = "sha_review_blocked"
    body = "- [ ] `READY FOR REVIEW` — pending review.\n- [x] `CHANGES REQUIRED` — review feedback open.\n"
    gh.pulls[275] = green_pr(275, sha, body)
    green_checks(gh, sha)

    with patch(
        "hunter_draft_promotion_signal.review_feedback_blockers",
        return_value=["unresolved review threads=1"],
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[275])

    assert gh.published[-1][1] == "pending"
    assert "unresolved review threads=1" in gh.published[-1][2]
    assert 275 not in gh.patched_bodies
    assert not gh.comments.get(275)


def test_changes_requested_review_blocks_promotion(gh):
    sha = "sha_changes_requested"
    body = "- [ ] `READY FOR REVIEW` — pending review.\n- [x] `CHANGES REQUIRED` — review feedback open.\n"
    gh.pulls[276] = green_pr(276, sha, body)
    green_checks(gh, sha)

    with patch(
        "hunter_draft_promotion_signal.review_feedback_blockers",
        return_value=["changes requested by reviewer"],
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[276])

    assert gh.published[-1][1] == "pending"
    assert "changes requested by reviewer" in gh.published[-1][2]
    assert 276 not in gh.patched_bodies


def test_review_feedback_is_rechecked_before_metadata_mutation(gh):
    sha = "sha_review_race"
    body = "- [ ] `READY FOR REVIEW` — pending review.\n- [x] `CHANGES REQUIRED` — waiting.\n"
    gh.pulls[277] = green_pr(277, sha, body)
    green_checks(gh, sha)

    with patch(
        "hunter_draft_promotion_signal.review_feedback_blockers",
        side_effect=[[], ["unresolved review threads=1"]],
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[277])

    assert gh.published[-1][1] == "pending"
    assert 277 not in gh.patched_bodies
    assert not gh.comments.get(277)


def test_draft_promotion_rejects_governance_success_for_superseded_base(gh):
    sha = "sha_superseded_base"
    body = "- [ ] `READY FOR REVIEW`\n- [x] `CHANGES REQUIRED`\n- [ ] `BLOCKED`\n"
    gh.pulls[279] = green_pr(279, sha, body)
    green_checks(gh, sha)
    gh.comments[279] = [
        {
            "id": 779,
            "body": f"<!-- hunter-draft-promotion:{sha} -->\n✅ **Hunter Draft Promotion:** stale success",
        }
    ]

    with patch.object(
        hunter_draft_promotion_signal.merge_readiness,
        "base_head_oids",
        return_value=("c" * 40, "b" * 40),
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[279])

    assert gh.published[-1][1] == "pending"
    assert "superseded revision" in gh.published[-1][2]
    assert not any(state == "success" for _sha, state, _description in gh.published)
    assert 279 not in gh.patched_bodies
    assert "invalidated" in gh.comments[279][0]["body"]
    assert "stale success" not in gh.comments[279][0]["body"]


@pytest.mark.parametrize(
    "description,expected_fragment",
    [
        ("Hunter Governance Review approved head", "carries no revision marker"),
        (f"[hgr:999:{'a' * 32}] Approved for head", "produced for PR #999"),
        (f"[hgr:280:{'a' * 32}] Approved for head", "superseded revision"),
    ],
)
def test_draft_promotion_rejects_unqualified_governance_evidence(gh, description, expected_fragment):
    sha = "sha_unqualified_governance"
    body = "- [ ] `READY FOR REVIEW`\n- [x] `CHANGES REQUIRED`\n- [ ] `BLOCKED`\n"
    gh.pulls[280] = green_pr(280, sha, body)
    green_checks(gh, sha)
    gh.statuses[sha] = [
        {"context": "Hunter Governance Review", "state": "success", "id": 1, "description": description}
    ]

    hunter_draft_promotion_signal.evaluate(gh.pulls[280])

    assert gh.published[-1][1] == "pending"
    assert expected_fragment in gh.published[-1][2]
    assert not any(state == "success" for _sha, state, _description in gh.published)
    assert 280 not in gh.patched_bodies
    assert not gh.comments.get(280)


def test_draft_promotion_final_gate_rechecks_governance_evidence(gh):
    sha = "sha_governance_race"
    body = "- [ ] `READY FOR REVIEW`\n- [x] `CHANGES REQUIRED`\n- [ ] `BLOCKED`\n"
    gh.pulls[281] = green_pr(281, sha, body)
    green_checks(gh, sha)
    gh.comments[281] = [
        {
            "id": 781,
            "body": f"<!-- hunter-draft-promotion:{sha} -->\n✅ **Hunter Draft Promotion:** stale success",
        }
    ]
    stale = {
        "context": "Hunter Governance Review",
        "state": "success",
        "id": 2,
        "description": f"[hgr:281:{'a' * 32}] Approved for head {sha}",
    }

    with (
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "load_issue",
            return_value=_governing_issue(),
        ),
        patch.object(
            hunter_draft_promotion_signal,
            "_governing_issue_number",
            return_value=276,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_pr_body",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "blocked_acceptance_criteria",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_ready_evidence",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_trace_against_state",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "all_commit_statuses",
            side_effect=[[gh.statuses[sha][0]], [stale]],
        ),
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[281])

    assert gh.published[-1][1] == "pending"
    assert "superseded revision" in gh.published[-1][2]
    assert not any(state == "success" for _sha, state, _description in gh.published)
    assert 281 not in gh.patched_bodies
    assert "invalidated" in gh.comments[281][0]["body"]
    assert "stale success" not in gh.comments[281][0]["body"]


def test_draft_promotion_final_gate_rechecks_required_checks(gh):
    sha = "sha_checks_race"
    body = "- [ ] `READY FOR REVIEW`\n- [x] `CHANGES REQUIRED`\n- [ ] `BLOCKED`\n"
    gh.pulls[282] = green_pr(282, sha, body)
    green_checks(gh, sha)
    gh.comments[282] = [
        {
            "id": 782,
            "body": f"<!-- hunter-draft-promotion:{sha} -->\n✅ **Hunter Draft Promotion:** stale success",
        }
    ]
    failing = [{"name": "Quality Gates", "status": "completed", "conclusion": "failure", "id": 999}]

    with (
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "load_issue",
            return_value=_governing_issue(),
        ),
        patch.object(
            hunter_draft_promotion_signal,
            "_governing_issue_number",
            return_value=276,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_pr_body",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "blocked_acceptance_criteria",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_ready_evidence",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_trace_against_state",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal,
            "exact_head_check_runs",
            side_effect=[[run for run in gh.check_runs[sha]], failing],
        ),
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[282])

    assert gh.published[-1][1] == "pending"
    assert "Quality Gates=failure" in gh.published[-1][2]
    assert not any(state == "success" for _sha, state, _description in gh.published)
    assert 282 not in gh.patched_bodies
    assert "invalidated" in gh.comments[282][0]["body"]
    assert "stale success" not in gh.comments[282][0]["body"]


def test_review_feedback_blockers_include_unacknowledged_top_level_comments():
    hunter_draft_promotion_signal.repo = "fafa33/Project-Hunter"
    hunter_draft_promotion_signal.token = "fake-token"
    with (
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "unresolved_review_thread_ids",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "current_changes_requested_reviewers",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "unacknowledged_top_level_comments",
            return_value=(101, 102),
        ),
    ):
        blockers = hunter_draft_promotion_signal.review_feedback_blockers(275)

    assert blockers == ["unacknowledged top-level comments=2"]


@pytest.mark.parametrize(
    ("unresolved", "changes_requested", "unacknowledged", "expected"),
    [
        (("THREAD-1",), (), (), "unresolved review threads=1"),
        ((), ("reviewer",), (), "changes requested by reviewer"),
        ((), (), (101,), "unacknowledged top-level comments=1"),
    ],
)
def test_canonical_merge_readiness_feedback_blocker_cannot_promote_draft(
    gh, unresolved, changes_requested, unacknowledged, expected
):
    pr_number = 278
    sha = f"sha_parity_{expected.split('=')[0].replace(' ', '_')}"
    body = "- [ ] `READY FOR REVIEW` — pending review.\n- [x] `CHANGES REQUIRED` — waiting.\n"
    gh.pulls[pr_number] = green_pr(pr_number, sha, body)
    green_checks(gh, sha)

    with (
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "unresolved_review_thread_ids",
            return_value=unresolved,
        ),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "current_changes_requested_reviewers",
            return_value=changes_requested,
        ),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "unacknowledged_top_level_comments",
            return_value=unacknowledged,
        ),
    ):
        blockers = hunter_draft_promotion_signal.review_feedback_blockers(pr_number)

    assert expected in blockers
    with patch("hunter_draft_promotion_signal.review_feedback_blockers", return_value=blockers):
        hunter_draft_promotion_signal.evaluate(gh.pulls[pr_number])

    assert gh.published[-1][1] == "pending"
    assert expected in gh.published[-1][2]
    assert pr_number not in gh.patched_bodies
    assert not gh.comments.get(pr_number)


def test_reconcile_open_draft_prs_rechecks_every_current_draft():
    drafts = [
        green_pr(275, "sha_a", "- [x] `READY FOR REVIEW` — a.\n"),
        green_pr(276, "sha_b", "- [x] `READY FOR REVIEW` — b.\n"),
    ]
    with (
        patch("hunter_draft_promotion_signal.open_draft_prs", return_value=drafts),
        patch("hunter_draft_promotion_signal.evaluate") as evaluate,
    ):
        hunter_draft_promotion_signal.reconcile_open_draft_prs()

    assert [call.args[0]["number"] for call in evaluate.call_args_list] == [275, 276]


def test_reconcile_open_draft_prs_isolates_failure_and_continues():
    drafts = [
        green_pr(275, "sha_a", "- [x] `READY FOR REVIEW` — a.\n"),
        green_pr(276, "sha_b", "- [x] `READY FOR REVIEW` — b.\n"),
    ]
    visited: list[int] = []

    def evaluate(pr):
        visited.append(pr["number"])
        if pr["number"] == 275:
            raise RuntimeError("invalid readiness declaration")

    with (
        patch("hunter_draft_promotion_signal.open_draft_prs", return_value=drafts),
        patch("hunter_draft_promotion_signal.evaluate", side_effect=evaluate),
    ):
        with pytest.raises(RuntimeError, match="PR #275: RuntimeError: invalid readiness declaration"):
            hunter_draft_promotion_signal.reconcile_open_draft_prs()

    assert visited == [275, 276]


def test_workflow_reconciles_when_review_feedback_changes():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-draft-promotion-signal.yml"
    ).read_text(encoding="utf-8")
    assert "pull_request_review:" in workflow
    assert "pull_request_review_comment:" in workflow
    assert "issue_comment:" in workflow
    assert "schedule:" in workflow
    assert 'cron: "*/5 * * * *"' in workflow


# --- GitHub infrastructure unavailability ------------------------------------


def _unavailable(message="HTTP 503: no server") -> hunter_draft_promotion_signal.transport.GitHubUnavailable:
    return hunter_draft_promotion_signal.transport.GitHubUnavailable(
        "GET reviews",
        attempts=3,
        last=hunter_draft_promotion_signal.transport.GitHubRequestError(message, category="transient", status_code=503),
    )


def test_review_feedback_infrastructure_unavailable_publishes_pending_no_ready(gh):
    sha = "sha_infra_unavailable"
    body = "- [ ] `READY FOR REVIEW`\n- [x] `CHANGES REQUIRED`\n- [ ] `BLOCKED`\n"
    gh.pulls[125] = green_pr(125, sha, body)
    green_checks(gh, sha)

    with (
        patch.object(
            hunter_draft_promotion_signal,
            "review_feedback_blockers",
            side_effect=_unavailable(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "load_issue",
            return_value=_governing_issue(),
        ),
        patch.object(
            hunter_draft_promotion_signal,
            "_governing_issue_number",
            return_value=276,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_pr_body",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "blocked_acceptance_criteria",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_ready_evidence",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_trace_against_state",
            return_value=None,
        ),
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[125])

    assert not any(state == "success" for _, state, _ in gh.published)
    assert gh.published[-1][1] == "pending"
    assert "review feedback infrastructure unavailable" in gh.published[-1][2]
    assert "[x] `READY FOR REVIEW`" not in (gh.patched_bodies.get(125) or "")


def test_node_resolution_404_in_readiness_reader_becomes_typed_pending(gh):
    sha = "sha_node_resolution_404"
    body = "- [ ] `READY FOR REVIEW`\n- [x] `CHANGES REQUIRED`\n- [ ] `BLOCKED`\n"
    gh.pulls[126] = green_pr(126, sha, body)
    green_checks(gh, sha)

    unavailable = hunter_draft_promotion_signal.transport.GitHubUnavailable(
        "GET pulls/126/reviews",
        attempts=3,
        last=hunter_draft_promotion_signal.transport.GitHubRequestError(
            "GitHub node-resolution 404: could not resolve to a node with the global id",
            category="node-resolution",
            status_code=404,
        ),
    )

    with (
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "unresolved_review_thread_ids",
            side_effect=unavailable,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "load_issue",
            return_value=_governing_issue(),
        ),
        patch.object(
            hunter_draft_promotion_signal,
            "_governing_issue_number",
            return_value=276,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_pr_body",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "blocked_acceptance_criteria",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_ready_evidence",
            return_value=None,
        ),
        patch.object(
            hunter_draft_promotion_signal.governance_preflight,
            "validate_trace_against_state",
            return_value=None,
        ),
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[126])

    assert not any(state == "success" for _, state, _ in gh.published)
    assert gh.published[-1][1] == "pending"
    assert "review feedback infrastructure unavailable" in gh.published[-1][2]


def test_review_unavailable_guard_is_binding_counterfactual(gh):
    """Without the typed guard the outage propagates and nothing is published."""
    sha = "sha_binding"
    body = "- [ ] `READY FOR REVIEW`\n- [x] `CHANGES REQUIRED`\n- [ ] `BLOCKED`\n"
    gh.pulls[127] = green_pr(127, sha, body)
    green_checks(gh, sha)

    with patch.object(
        hunter_draft_promotion_signal,
        "review_blockers_or_unavailable",
        side_effect=_unavailable(),
    ):
        with pytest.raises(hunter_draft_promotion_signal.transport.GitHubUnavailable):
            hunter_draft_promotion_signal.evaluate(gh.pulls[127])

    assert gh.published == []


def test_direct_event_path_fails_closed_with_typed_exit(gh, monkeypatch, tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"pull_request": {"number": 999}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.setenv("GH_TOKEN", "fake-token")

    with patch.object(
        hunter_draft_promotion_signal,
        "evaluate",
        side_effect=_unavailable(),
    ):
        with pytest.raises(SystemExit) as raised:
            hunter_draft_promotion_signal.main()

    assert raised.value.code == 1


def test_sweep_isolates_unavailable_pr_and_reports_typed_failure(gh):
    drafts = [
        green_pr(275, "sha_a", "- [x] `READY FOR REVIEW` — a.\n"),
        green_pr(276, "sha_b", "- [x] `READY FOR REVIEW` — b.\n"),
    ]
    visited: list[int] = []

    def evaluate(pr):
        visited.append(pr["number"])
        if pr["number"] == 275:
            raise _unavailable()

    with (
        patch("hunter_draft_promotion_signal.open_draft_prs", return_value=drafts),
        patch("hunter_draft_promotion_signal.evaluate", side_effect=evaluate),
    ):
        with pytest.raises(RuntimeError, match="PR #275: GitHub infrastructure unavailable"):
            hunter_draft_promotion_signal.reconcile_open_draft_prs()

    assert visited == [275, 276]
