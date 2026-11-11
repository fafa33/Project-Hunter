"""Regression: the owner-approved #577/#703 positive-outcome writer never fabricates evidence.

``lifecycle._verified_e2e_outcome_evidence`` is a pure function: it binds a ``verified_e2e_outcome``
record only to facts already present in the live GitHub pull request, the signed Issue-577 ledger's
own ``COMPLETED`` authorization, and a live hosted admission run. Every test here exercises that
binding directly, with no network and no signature verification (the ledger-read layer that performs
signature verification is exercised separately in ``test_issue_agent_knowledge.py``).
"""

from __future__ import annotations

import copy
from typing import Any

import hunter_issue_agent_lifecycle as lifecycle
import pytest

from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_control import FactsUnavailable

ISSUE = 577
PR = 703
HEAD = "8077437e0b0e27547ac7b7bd2fb1520dd3c9d849"
BASE = "48f1ddfc306b576a1ede4c40c12588dc64c3ee9b"
MERGE_COMMIT = "32b2db0a84d411143ad99e51fa7b547447c7a44c"
VALIDATOR_RUN_ID = 42510436813
ADMISSION_RUN_ID = 42526727825
RESULT_SHA256 = "5b2c87c6b5fbafdacd3deb5d11a5120fd58b1507b05aa799fe815278fa06af94"
AUTHORIZATION_ID = "hunter-issue-agent-authorization:6dda24ebe4c5e23a496f720b07ffe64951a344d7d5f594b8dcfad4fd24f081c2"


def _record(*, state_name: str, run_id: int, run_attempt: int, job: str, head_sha: str) -> dict[str, Any]:
    return {
        "state": state_name,
        "recorded_by": {
            "head_sha": head_sha,
            "job": job,
            "role": job,
            "run_attempt": run_attempt,
            "run_id": run_id,
            "workflow_path": ".github/workflows/hunter-issue-agent-trigger.yml",
        },
    }


def _authorization(**overrides: Any) -> state.AuthorizationView:
    completed = {
        "pull_request_number": PR,
        "pull_request_head_sha": HEAD,
        "preflight_run_id": 42511703461,
        "preflight_conclusion": "success",
    }
    validated = {
        "result_sha256": RESULT_SHA256,
        "validator_run_id": VALIDATOR_RUN_ID,
        "unsigned_commit_sha": "9182aa0694ba3e6e91cbc81594896f59cce79e8e",
    }
    completed.update(overrides.pop("completed", {}))
    validated.update(overrides.pop("validated", {}))
    records = overrides.pop(
        "records",
        [
            _record(
                state_name=state.VALIDATED,
                run_id=VALIDATOR_RUN_ID,
                run_attempt=1,
                job="record-validation",
                head_sha=BASE,
            ),
            _record(state_name=state.COMPLETED, run_id=42512947286, run_attempt=1, job="record", head_sha=BASE),
        ],
    )
    return state.AuthorizationView(
        authorization_id=overrides.pop("authorization_id", AUTHORIZATION_ID),
        state=overrides.pop("state", state.COMPLETED),
        records=records,
        evidence={state.COMPLETED: completed, state.VALIDATED: validated, **overrides.pop("evidence", {})},
    )


def _pull(**overrides: Any) -> dict[str, Any]:
    pull = {
        "number": PR,
        "merged_at": "2026-11-08T22:05:50Z",
        "head": {"sha": HEAD},
        "base": {"sha": BASE},
        "merge_commit_sha": MERGE_COMMIT,
    }
    pull.update(overrides)
    return pull


def _admission_run(**overrides: Any) -> dict[str, Any]:
    run = {"id": ADMISSION_RUN_ID, "name": "Hunter Candidate Admission", "conclusion": "success"}
    run.update(overrides)
    return run


def _build(**kwargs: Any) -> dict[str, Any]:
    return lifecycle._verified_e2e_outcome_evidence(
        issue=kwargs.pop("issue", ISSUE),
        pull_request=kwargs.pop("pull_request", PR),
        pull=kwargs.pop("pull", _pull()),
        authorization=kwargs.pop("authorization", _authorization()),
        admission_run=kwargs.pop("admission_run", _admission_run()),
    )


def test_authentic_577_fixture_produces_expected_evidence() -> None:
    evidence = _build()
    assert evidence == {
        "issue_number": ISSUE,
        "pull_request_number": PR,
        "head_sha": HEAD,
        "base_sha": BASE,
        "merge_commit_sha": MERGE_COMMIT,
        "lifecycle_run_id": VALIDATOR_RUN_ID,
        "lifecycle_run_attempt": 1,
        "authoritative_job": "record-validation",
        "proof_digest": RESULT_SHA256,
        "conclusion": "success",
        "admission_run_id": ADMISSION_RUN_ID,
        "source": "github-authenticated-canonical-proof",
    }


@pytest.mark.parametrize(("issue", "pr"), [(577, 1), (1, 703), (705, 703)])
def test_rejects_any_issue_or_pr_outside_the_owner_approved_pair(issue: int, pr: int) -> None:
    with pytest.raises(lifecycle.LifecycleRefused):
        _build(issue=issue, pull_request=pr)


def test_rejects_an_authorization_that_is_not_completed() -> None:
    with pytest.raises(lifecycle.LifecycleRefused):
        _build(authorization=_authorization(state=state.FAILED))


def test_rejects_a_completed_authorization_missing_validated_evidence() -> None:
    authorization = _authorization()
    del authorization.evidence[state.VALIDATED]
    with pytest.raises(lifecycle.LifecycleRefused):
        _build(authorization=authorization)


def test_rejects_an_unmerged_pull_request() -> None:
    with pytest.raises(FactsUnavailable):
        _build(pull=_pull(merged_at=None))


def test_rejects_a_missing_merge_commit() -> None:
    with pytest.raises(FactsUnavailable):
        _build(pull=_pull(merge_commit_sha=None))


def test_rejects_a_forged_head_sha_that_disagrees_with_the_signed_completion() -> None:
    with pytest.raises(lifecycle.LifecycleRefused):
        _build(pull=_pull(head={"sha": "f" * 40}))


def test_rejects_a_stale_base_sha_that_moved_since_the_signed_run() -> None:
    with pytest.raises(lifecycle.LifecycleRefused):
        _build(pull=_pull(base={"sha": "a" * 40}))


def test_rejects_a_completion_bound_to_another_pull_request() -> None:
    with pytest.raises(lifecycle.LifecycleRefused):
        _build(authorization=_authorization(completed={"pull_request_number": 1}))


def test_rejects_a_non_success_preflight_conclusion() -> None:
    with pytest.raises(lifecycle.LifecycleRefused):
        _build(authorization=_authorization(completed={"preflight_conclusion": "failure"}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": 1},
        {"name": "some-other-workflow"},
        {"conclusion": "failure"},
    ],
)
def test_rejects_an_admission_run_that_cannot_be_authenticated(overrides: dict[str, Any]) -> None:
    with pytest.raises(lifecycle.LifecycleRefused):
        _build(admission_run=_admission_run(**overrides))


def test_rejects_an_internally_inconsistent_validator_run_identity() -> None:
    authorization = _authorization()
    tampered = copy.deepcopy(authorization.records)
    tampered[0]["recorded_by"]["run_id"] = VALIDATOR_RUN_ID + 1
    authorization.records = tampered
    with pytest.raises(lifecycle.LifecycleRefused):
        _build(authorization=authorization)


def test_evidence_conforms_to_the_signed_knowledge_ledger_schema() -> None:
    from hunter.automation import issue_agent_knowledge as knowledge

    evidence = _build()
    knowledge.EVIDENCE_SCHEMAS["verified_e2e_outcome"](evidence, "evidence")
