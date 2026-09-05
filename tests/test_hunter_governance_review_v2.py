from __future__ import annotations

import importlib
from pathlib import Path

core = importlib.import_module("hunter_governance_review_v2")
transport = importlib.import_module("hunter_github_transport")


HEAD = "b" * 40


def _pr(mergeable: bool | None) -> dict:
    return {
        "state": "open",
        "mergeable": mergeable,
        "title": "no canonical title or issue identity",
        "body": "no matrix, no readiness declaration, no reaction ceremony",
        "head": {"sha": HEAD, "ref": "feature/no-governance-metadata"},
        "base": {"sha": "c" * 40, "ref": "main"},
    }


def test_governance_review_ignores_process_metadata(monkeypatch):
    published = []
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(True))
    monkeypatch.setattr(core, "candidate_admission", lambda *_args: ("success", "admitted"))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0

    assert published[0][3] == "success"


def test_governance_review_fails_real_merge_conflict(monkeypatch):
    published = []
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(False))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0

    assert published[0][3] == "failure"
    assert "merge conflicts" in published[0][4]


def test_governance_review_waits_for_unknown_mergeability(monkeypatch):
    published = []
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(None))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0

    assert published[0][3] == "pending"


def test_governance_review_skips_non_main_target(monkeypatch):
    published = []
    pr = _pr(True)
    pr["base"]["ref"] = "release"
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: pr)
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0
    assert published == []


def test_governance_review_skips_disposition_check_for_closed_pr(monkeypatch):
    published = []
    pr = _pr(True)
    pr["state"] = "closed"
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: pr)
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (False, "Unresolved finding RFD-ERR"))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0
    assert published == []


def test_governance_review_skips_disposition_check_for_non_main_pr(monkeypatch):
    published = []
    pr = _pr(True)
    pr["base"]["ref"] = "release"
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: pr)
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (False, "Unresolved finding RFD-ERR"))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0
    assert published == []


def test_governance_review_fails_closed_for_open_main_pr_with_unresolved_disposition(monkeypatch):
    published = []
    pr = _pr(True)
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: pr)
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (False, "Unresolved finding RFD-ERR"))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0
    assert len(published) == 1
    assert published[0][3] == "failure"
    assert "Unresolved finding RFD-ERR" in published[0][4]


def test_candidate_admission_tests_first_red_success_stays_draft(monkeypatch):
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, (), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("tests-first-red", None))

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "failure"
    assert "Draft-only" in description


def test_changed_file_evidence_preserves_deletion_and_rename_semantics(monkeypatch):
    base_blob = "a" * 40
    renamed_blob = "b" * 40
    monkeypatch.setattr(
        core,
        "request_json",
        lambda *_args: [
            {"filename": "docs/deleted.md", "status": "removed", "sha": base_blob},
            {
                "filename": "docs/new.md",
                "previous_filename": ".github/workflows/old.yml",
                "status": "renamed",
                "sha": renamed_blob,
            },
        ],
    )

    ok, files, error = core.read_pr_changed_files("fafa33/Project-Hunter", "token", 410)

    assert ok is True and error is None
    assert files == (
        core.PullRequestFile("removed", "docs/deleted.md", blob_sha=""),
        core.PullRequestFile("renamed", "docs/new.md", ".github/workflows/old.yml", renamed_blob),
    )


def test_protected_preflight_ordinary_candidate_cannot_self_authorize(monkeypatch):
    monkeypatch.setattr(
        core,
        "read_pr_changed_paths",
        lambda *_args: (True, ("scripts/hunter_pr_preflight.py",), None),
    )
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))
    monkeypatch.setattr(
        core,
        "verify_code_write_ingress_provenance",
        lambda *_args: ("success", "ingress provenance verified"),
    )
    monkeypatch.setattr(core, "verify_pre_ready_hostile_review", lambda *_args: ("success", "reviewed"))
    monkeypatch.setattr(
        core,
        "read_trusted_upgrade_status",
        lambda *_args: ("missing", "trusted proof missing"),
    )

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "failure"
    assert description == "trusted proof missing"


def test_workflow_uses_only_trusted_v2_controller_without_bootstrap():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-review.yml"
    ).read_text(encoding="utf-8")

    assert "Checkout installation controller" not in workflow
    assert "installation-engine" not in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "persist-credentials: false" in workflow
    assert 'PR_NUMBER} = "283"' not in workflow
    assert "python -m hunter_governance_review" not in workflow
    assert "bootstrap" not in workflow.lower()
    assert "hunter_governance_review_v2.py" in workflow


def test_reconcile_continues_after_one_pr_failure_and_drops_checkout_credentials():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-reconcile.yml"
    ).read_text(encoding="utf-8")

    assert "persist-credentials: false" in workflow
    assert "failures=0" in workflow
    assert "if ! python scripts/hunter_governance_review_v2.py" in workflow
    assert "failures=1" in workflow
    assert 'exit "${failures}"' in workflow


ANCESTOR = "d" * 40
FLOOR = "e" * 40
SUCCESSFUL_RUN = {
    "head_sha": HEAD,
    "name": core.PRE_PR_WORKFLOW_NAME,
    "path": core.PRE_PR_WORKFLOW_PATH,
    "event": "push",
    "status": "completed",
    "conclusion": "success",
    "id": 100,
}
TRUSTED_RUN_ID = 207
TRUSTED_UPGRADE_RUN = {
    "id": TRUSTED_RUN_ID,
    "name": core.TRUSTED_UPGRADE_WORKFLOW_NAME,
    "path": core.TRUSTED_UPGRADE_WORKFLOW_PATH,
    "event": "pull_request_target",
    "head_sha": HEAD,
    "status": "completed",
    "conclusion": "success",
    "pull_requests": [{"number": 501, "head": {"sha": HEAD}}],
}


def _signed(sha: str, signer: str = "claude") -> dict:
    return {
        "sha": sha,
        "committer": {"login": signer},
        "commit": {"verification": {"verified": True, "reason": "valid"}},
    }


def _unsigned(sha: str, signer: str = "fafa33") -> dict:
    return {
        "sha": sha,
        "committer": {"login": signer},
        "commit": {"verification": {"verified": False, "reason": "unsigned"}},
    }


def _admission(monkeypatch, commits: list[dict], *, hosted_ci: bool = False, policy=None, extra=None):
    """Drive candidate_admission over a stubbed PR commit range."""
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_args: (True, (core.PullRequestFile("modified", "src/hunter/cli.py", blob_sha="a" * 40),), None),
    )
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, ("src/hunter/cli.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))
    monkeypatch.setattr(
        core,
        "load_ingress_provenance_policy",
        lambda: policy if policy is not None else (frozenset({"claude", "fafa33"}), "", None),
    )

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "pulls/501/commits" in path:
            return commits
        if extra is not None:
            handled, value = extra(path)
            if handled:
                return value
        # Trusted evidence the connector-ingress re-derivation reads. These
        # fixtures describe an ordinary clone-capable candidate: a branch outside
        # the connector namespace carrying no authorization receipt, so the
        # connector check is a no-op and these cases keep testing exactly the
        # signature/branch-preflight contract they were written for.
        if path.startswith("pulls/501"):
            return {"head": {"ref": "claude/clone-written", "sha": HEAD}, "base": {"ref": "main"}}
        if path.startswith("contents/.hunter/"):
            raise transport.GitHubRequestError("Not Found", category="permanent", status_code=404)
        if "statuses" in path:
            # The active connector grant makes the trusted hosted exact-head
            # proof mandatory for every candidate, clone-written included.
            return [
                {
                    "id": 7,
                    "context": core._upgrade_status_context(501),
                    "state": "success",
                    "creator": {"login": core.TRUSTED_STATUS_CREATOR, "type": "Bot"},
                    "target_url": f"https://github.com/fafa33/Project-Hunter/actions/runs/{TRUSTED_RUN_ID}",
                }
            ]
        if path == f"actions/runs/{TRUSTED_RUN_ID}":
            return TRUSTED_UPGRADE_RUN
        if hosted_ci and "actions/runs" in path:
            return {"workflow_runs": [SUCCESSFUL_RUN]}
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)
    # The Issue #412 pre-ready hostile review gate is an independent admission
    # prerequisite with its own regression suite
    # (tests/test_issue_412_prevention_gate.py). Stubbing it keeps these fixtures
    # on the signature/branch-preflight contract they were written for.
    monkeypatch.setattr(core, "verify_pre_ready_hostile_review", lambda *_args: ("success", "reviewed"))
    return core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)


def test_custom_identity_api_only_write_is_rejected(monkeypatch) -> None:
    """An ordinary committer identity is caller-supplied and proves no ingress."""
    state, description = _admission(monkeypatch, [_unsigned(HEAD, signer="fafa33")])

    assert state == "failure"
    assert "no verified pre-push ingress signature" in description


def test_api_written_ancestor_is_not_concealed_by_clone_authored_tip(monkeypatch) -> None:
    state, description = _admission(monkeypatch, [_unsigned(ANCESTOR), _signed(HEAD)])

    assert state == "failure"
    assert ANCESTOR[:10] in description
    assert "no verified pre-push ingress signature" in description


def test_missing_trusted_provenance_fails_closed(monkeypatch) -> None:
    state, description = _admission(
        monkeypatch,
        [_signed(HEAD)],
        policy=(frozenset(), "", "code-write policy declares no authorized ingress signers"),
    )

    assert state == "failure"
    assert "no authorized ingress signers" in description


def test_signature_from_unauthorized_signer_is_rejected(monkeypatch) -> None:
    state, description = _admission(monkeypatch, [_signed(HEAD, signer="attacker")])

    assert state == "failure"
    assert "unauthorized ingress signer attacker" in description


def test_stale_proof_for_older_sha_cannot_authorize_newer_head(monkeypatch) -> None:
    """A proof is the signature over its own commit object, so it cannot move."""
    state, description = _admission(monkeypatch, [_signed(ANCESTOR), _unsigned(HEAD)])

    assert state == "failure"
    assert HEAD[:10] in description


def test_head_absent_from_commit_range_evidence_fails_closed(monkeypatch) -> None:
    state, description = _admission(monkeypatch, [_signed(ANCESTOR)])

    assert state == "failure"
    assert "exact head is absent from the PR commit range" in description


def test_hosted_ci_success_cannot_override_missing_local_provenance(monkeypatch) -> None:
    state, description = _admission(monkeypatch, [_unsigned(HEAD)], hosted_ci=True)

    assert state == "failure"
    assert "no verified pre-push ingress signature" in description


def test_clone_capable_provenance_for_every_commit_progresses(monkeypatch) -> None:
    state, description = _admission(monkeypatch, [_signed(ANCESTOR), _signed(HEAD)], hosted_ci=True)

    assert state == "success"
    assert "Exact-head branch preflight passed" in description


def test_commits_reachable_from_declared_floor_predate_the_regime(monkeypatch) -> None:
    """Pre-authority history is exempt; everything after the floor is not."""

    def extra(path):
        if f"compare/{FLOOR}...{HEAD}" in path:
            return True, {"commits": [{"sha": HEAD}]}
        return False, None

    state, description = _admission(
        monkeypatch,
        [_unsigned(FLOOR), _signed(HEAD)],
        hosted_ci=True,
        policy=(frozenset({"claude"}), FLOOR, None),
        extra=extra,
    )

    assert state == "success"
    assert "Exact-head branch preflight passed" in description


def test_declared_floor_does_not_exempt_commits_beyond_it(monkeypatch) -> None:
    def extra(path):
        if f"compare/{FLOOR}...{HEAD}" in path:
            return True, {"commits": [{"sha": ANCESTOR}, {"sha": HEAD}]}
        return False, None

    state, description = _admission(
        monkeypatch,
        [_unsigned(FLOOR), _unsigned(ANCESTOR), _signed(HEAD)],
        policy=(frozenset({"claude"}), FLOOR, None),
        extra=extra,
    )

    assert state == "failure"
    assert ANCESTOR[:10] in description


def test_unavailable_pre_authority_range_evidence_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        core,
        "read_commits_beyond_attestation_floor",
        lambda *_args: (False, frozenset(), "commit range comparison payload is malformed"),
    )
    state, description = _admission(
        monkeypatch,
        [_unsigned(FLOOR), _signed(HEAD)],
        policy=(frozenset({"claude"}), FLOOR, None),
    )

    assert state == "failure"
    assert "pre-authority range evidence is unavailable" in description


def test_unavailable_commit_range_evidence_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_args: (True, (core.PullRequestFile("modified", "src/hunter/cli.py", blob_sha="a" * 40),), None),
    )
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, ("src/hunter/cli.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))
    monkeypatch.setattr(core, "read_pr_commits", lambda *_args: (False, (), "GitHub request error: 500"))

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "failure"
    assert "commit-range ingress evidence is unavailable" in description


def test_ingress_provenance_requires_pr_bound_range_evidence() -> None:
    state, description = core.verify_code_write_ingress_provenance("fafa33/Project-Hunter", "token", HEAD, None)

    assert state == "failure"
    assert "requires PR-bound commit-range evidence" in description


def test_author_identity_cannot_stand_in_for_the_signing_committer() -> None:
    """Only the committer login is cryptographically bound by a verified signature."""
    entry = {
        "sha": HEAD,
        "author": {"login": "claude"},
        "committer": {"login": "attacker"},
        "commit": {"verification": {"verified": True, "reason": "valid"}},
    }

    assert core._ingress_signer(entry) == "attacker"


def test_canonical_policy_declares_authorized_signers_and_floor() -> None:
    signers, floor, error = core.load_ingress_provenance_policy()

    assert error is None
    assert "claude" in signers
    assert core._is_commit_sha(floor)


def test_import_ordering_recurrence_prh001_cannot_reach_admitted_candidate_without_canonical_gate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_args: (
            True,
            (core.PullRequestFile("modified", "tests/test_issue_agent_trigger.py", blob_sha="a" * 40),),
            None,
        ),
    )
    monkeypatch.setattr(
        core, "read_pr_changed_paths", lambda *_args: (True, ("tests/test_issue_agent_trigger.py",), None)
    )
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))
    monkeypatch.setattr(core, "load_ingress_provenance_policy", lambda: (frozenset({"claude"}), "", None))

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "pulls/501/commits" in path:
            return [_unsigned(HEAD)]
        # An ordinary clone-path candidate: outside the connector namespace and
        # carrying no authorization receipt, so the verified-signature regime
        # applies to it (Issue #409 decides the channel before the proof).
        if path.startswith("pulls/501"):
            return {"head": {"ref": "claude/ordinary-candidate", "sha": HEAD}, "base": {"ref": "main"}}
        if path.startswith("contents/"):
            raise transport.GitHubRequestError("Not Found", category="permanent", status_code=404)
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "failure"
    assert "no verified pre-push ingress signature" in description


def test_legitimate_api_only_read_review_metadata_operations_allowed(monkeypatch) -> None:
    published = []

    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(True))
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (True, ""))
    monkeypatch.setattr(core, "candidate_admission", lambda *_args: ("success", "admitted"))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    result = core.review("fafa33/Project-Hunter", "token", 501)

    assert result == 0
    assert len(published) == 1
    assert published[0][3] == "success"


def test_malformed_commit_range_sha_fails_closed(monkeypatch) -> None:
    state, description = _admission(monkeypatch, [{"sha": "not-a-sha"}, _signed(HEAD)])

    assert state == "failure"
    assert "malformed commit SHA" in description


def test_non_boolean_verified_flag_cannot_satisfy_the_gate(monkeypatch) -> None:
    """Only a real verified=True survives; a truthy stand-in must not."""
    entry = {
        "sha": HEAD,
        "committer": {"login": "claude"},
        "commit": {"verification": {"verified": "true", "reason": "valid"}},
    }

    state, description = _admission(monkeypatch, [entry])

    assert state == "failure"
    assert "no verified pre-push ingress signature" in description


def test_missing_verification_block_fails_closed(monkeypatch) -> None:
    state, description = _admission(monkeypatch, [{"sha": HEAD, "committer": {"login": "claude"}, "commit": {}}])

    assert state == "failure"
    assert "carries no ingress signature evidence" in description


def test_authorized_signer_matching_ignores_case_and_padding(monkeypatch) -> None:
    entry = {
        "sha": HEAD,
        "committer": {"login": "  Claude  "},
        "commit": {"verification": {"verified": True, "reason": "valid"}},
    }

    state, _description = _admission(monkeypatch, [entry], hosted_ci=True)

    assert state == "success"


def test_web_flow_merge_commit_is_not_an_authorized_ingress_signer(monkeypatch) -> None:
    """GitHub-side merges are signed and verified, but they are still API writes."""
    entry = {
        "sha": HEAD,
        "committer": {"login": "web-flow"},
        "commit": {"verification": {"verified": True, "reason": "valid"}},
    }

    state, description = _admission(monkeypatch, [entry])

    assert state == "failure"
    assert "unauthorized ingress signer web-flow" in description
