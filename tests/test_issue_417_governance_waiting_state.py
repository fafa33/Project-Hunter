"""Issue #417: a proof that has not finished is a dependency, not a defect.

During PR #416 ``Hunter Governance Review`` published red for about ten minutes
while the trusted exact-head proof that candidate admission requires was running
normally. Fail-closed was correct; calling it a failure was not. A red status
says "something is wrong with this candidate", so it invites repair attempts and
reruns for a candidate whose only unmet condition is time.

The distinction these fixtures pin is deterministic and narrow:

``waiting``
    trusted proof is required, the head is known exactly, an eligible trusted
    run for *that* head is queued or in progress, and nothing else is blocking.

``failure``
    everything else -- the run failed, was cancelled, is unusable, belongs to
    another head or workflow, the evidence is malformed, no eligible run exists
    at all, or an independent governance blocker is present at the same time.

Waiting is never success, and never satisfies admission or merge readiness.
"""

from __future__ import annotations

from typing import Any

import hunter_governance_review_v2 as core
import hunter_merge_readiness_v2 as readiness
import pytest

HEAD = "a" * 40
FOREIGN_HEAD = "b" * 40
PR = 416
REPO = "fafa33/Project-Hunter"
RUN_ID = 34028443284


def _run(**overrides: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "id": RUN_ID,
        "name": core.TRUSTED_UPGRADE_WORKFLOW_NAME,
        "path": core.TRUSTED_UPGRADE_WORKFLOW_PATH,
        "event": "pull_request_target",
        "head_sha": HEAD,
        "status": "in_progress",
        "conclusion": None,
        "pull_requests": [{"number": PR, "head": {"sha": HEAD}}],
    }
    run.update(overrides)
    return run


def _install(monkeypatch: pytest.MonkeyPatch, *, statuses: Any, runs: Any, run_detail: Any = None) -> list[str]:
    """Route the controller's two GitHub reads at fixed, inspectable payloads."""

    seen: list[str] = []

    def fake_request_json(repository: str, token: str, method: str, path: str, payload: Any = None) -> Any:
        seen.append(path)
        if path.startswith("commits/"):
            return statuses
        if path.startswith("actions/runs?"):
            return runs
        if path.startswith("actions/runs/"):
            if isinstance(run_detail, Exception):
                raise run_detail
            return run_detail
        raise AssertionError(f"unexpected request path: {path}")

    monkeypatch.setattr(core, "request_json", fake_request_json)
    return seen


def _published_status(**overrides: Any) -> dict[str, Any]:
    status: dict[str, Any] = {
        "id": 7,
        "context": core._upgrade_status_context(PR),
        "state": "success",
        "creator": {"login": core.TRUSTED_STATUS_CREATOR, "type": "Bot"},
        "target_url": f"https://github.com/{REPO}/actions/runs/{RUN_ID}",
    }
    status.update(overrides)
    return status


def _resolve() -> tuple[str, str]:
    return core.read_trusted_upgrade_status(REPO, "token", HEAD, PR)


# --------------------------------------------------------------------------
# A. An eligible trusted run for this exact head is still running -> WAITING
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_an_eligible_active_trusted_run_is_waiting_not_failure(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    """The PR #416 symptom, in both states GitHub reports before completion."""
    _install(monkeypatch, statuses=[], runs={"workflow_runs": [_run(status=status)]})

    state, description = _resolve()

    assert state == "pending"
    assert description == core.TRUSTED_PROOF_WAITING_DESCRIPTION


def test_the_waiting_description_names_the_dependency() -> None:
    """An operator must be able to tell 'not finished' from 'failed' at a glance."""
    assert core.TRUSTED_PROOF_WAITING_DESCRIPTION == "Waiting for trusted exact-head preflight proof"


def test_waiting_propagates_through_candidate_admission_as_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Waiting reaches the caller as pending, and is never reported as success."""
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_a: (True, ["scripts/hunter_pr_preflight.py"], ""))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_a: ("normal", None))
    monkeypatch.setattr(core, "verify_code_write_ingress_provenance", lambda *_a: ("success", "ingress ok"))
    monkeypatch.setattr(core, "verify_pre_ready_hostile_review", lambda *_a: ("success", "reviewed"))
    monkeypatch.setattr(
        core,
        "read_trusted_upgrade_status",
        lambda *_a: ("pending", core.TRUSTED_PROOF_WAITING_DESCRIPTION),
    )

    state, description = core.candidate_admission(REPO, "token", HEAD, PR)

    assert state == "pending"
    assert state != "success"
    assert description == core.TRUSTED_PROOF_WAITING_DESCRIPTION


# --------------------------------------------------------------------------
# B/C. Terminal outcomes are unchanged once the run finishes.
# --------------------------------------------------------------------------


def test_the_same_head_succeeds_once_the_trusted_proof_lands(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR #416's actual sequence: waiting, then success, with no new commit.

    The candidate is byte-identical across both halves of this fixture. Only the
    hosted evidence advances, which is exactly the claim Issue #417 makes about
    the red state it replaces.
    """
    active = {"workflow_runs": [_run(status="in_progress")]}
    _install(monkeypatch, statuses=[], runs=active)
    assert _resolve() == ("pending", core.TRUSTED_PROOF_WAITING_DESCRIPTION)

    completed = _run(status="completed", conclusion="success")
    _install(monkeypatch, statuses=[_published_status()], runs=active, run_detail=completed)

    state, description = _resolve()

    assert state == "success"
    assert description == "Exact-head trusted candidate preflight validation passed."


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "startup_failure", "stale"])
def test_a_terminated_trusted_run_is_failure_not_waiting(monkeypatch: pytest.MonkeyPatch, conclusion: str) -> None:
    _install(
        monkeypatch,
        statuses=[_published_status()],
        runs={"workflow_runs": []},
        run_detail=_run(status="completed", conclusion=conclusion),
    )

    state, description = _resolve()

    assert state == "failure"
    assert conclusion in description


def test_a_failed_published_status_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, statuses=[_published_status(state="failure")], runs={"workflow_runs": []})

    state, _description = _resolve()

    assert state == "failure"


# --------------------------------------------------------------------------
# D. Ineligible runs never earn the waiting state.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("head_sha", FOREIGN_HEAD, id="foreign-head"),
        pytest.param("name", "Hunter / Pre-PR Preflight", id="wrong-workflow-name"),
        pytest.param("path", ".github/workflows/impostor.yml", id="wrong-workflow-path"),
        pytest.param("event", "workflow_dispatch", id="wrong-event"),
    ],
)
def test_an_ineligible_active_run_is_not_a_waiting_dependency(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    """Identity is matched on every field, so a lookalike cannot buy time.

    Each of these is an active run that would be waiting if the check were only
    on the field left alone; none of them is proof for *this* candidate, so the
    absence of a real eligible run is reported as it was before.
    """
    _install(monkeypatch, statuses=[], runs={"workflow_runs": [_run(**{field: value})]})

    state, description = _resolve()

    assert state == "missing"
    assert "missing" in description


def test_an_active_run_not_bound_to_this_pull_request_is_not_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        statuses=[],
        runs={"workflow_runs": [_run(pull_requests=[{"number": 999, "head": {"sha": HEAD}}])]},
    )

    assert _resolve()[0] == "missing"


def test_an_active_run_whose_binding_names_another_head_is_not_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        statuses=[],
        runs={"workflow_runs": [_run(pull_requests=[{"number": PR, "head": {"sha": FOREIGN_HEAD}}])]},
    )

    assert _resolve()[0] == "missing"


def test_an_untrusted_status_publisher_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        statuses=[_published_status(creator={"login": "someone-else", "type": "User"})],
        runs={"workflow_runs": []},
    )

    state, description = _resolve()

    assert state == "failure"
    assert "untrusted publisher" in description


def test_a_status_pointing_at_a_foreign_run_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid-looking status cannot borrow another head's completed run."""
    _install(
        monkeypatch,
        statuses=[_published_status()],
        runs={"workflow_runs": []},
        run_detail=_run(status="completed", conclusion="success", head_sha=FOREIGN_HEAD),
    )

    state, description = _resolve()

    assert state == "failure"
    assert "does not identify the trusted exact-head workflow" in description


# --------------------------------------------------------------------------
# E. Malformed, unavailable, or absent evidence all fail closed.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "runs",
    [
        pytest.param("not-a-mapping", id="payload-not-an-object"),
        pytest.param({"workflow_runs": "not-a-list"}, id="runs-not-a-list"),
    ],
)
def test_malformed_run_evidence_is_failure_not_waiting(monkeypatch: pytest.MonkeyPatch, runs: Any) -> None:
    _install(monkeypatch, statuses=[], runs=runs)

    state, description = _resolve()

    assert state == "failure"
    assert "malformed" in description


def test_unavailable_run_evidence_is_failure_not_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lookup that cannot answer must not answer 'merely waiting'."""

    def exploding(repository: str, token: str, method: str, path: str, payload: Any = None) -> Any:
        if path.startswith("commits/"):
            return []
        raise RuntimeError("GitHub is unavailable")

    monkeypatch.setattr(core, "request_json", exploding)

    state, description = _resolve()

    assert state == "failure"
    assert "unavailable" in description


def test_no_eligible_trusted_run_at_all_stays_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Where trusted proof is required and no path to it exists, that is a defect."""
    _install(monkeypatch, statuses=[], runs={"workflow_runs": []})

    state, description = _resolve()

    assert state == "missing"
    assert "missing" in description


def test_a_completed_eligible_run_without_a_status_is_not_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Waiting is bounded by run state: a finished run is no longer a dependency."""
    _install(monkeypatch, statuses=[], runs={"workflow_runs": [_run(status="completed", conclusion="success")]})

    assert _resolve()[0] == "missing"


def test_malformed_status_evidence_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, statuses={"not": "a list"}, runs={"workflow_runs": []})

    state, description = _resolve()

    assert state == "failure"
    assert "malformed" in description


# --------------------------------------------------------------------------
# An independent blocker outranks the wait.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("blocker", "verdict"),
    [
        pytest.param("read_head_preflight_mode", ("tests-first-red", None), id="tests-first-red-head"),
        pytest.param("verify_code_write_ingress_provenance", ("failure", "unsigned commit"), id="ingress-defect"),
        pytest.param("verify_pre_ready_hostile_review", ("failure", "review is stale"), id="stale-review"),
    ],
)
def test_a_real_blocker_alongside_an_active_run_is_failure_not_waiting(
    monkeypatch: pytest.MonkeyPatch, blocker: str, verdict: tuple[str, Any]
) -> None:
    """Waiting describes a candidate whose *only* unmet condition is time.

    The trusted run is active in every case here, so a controller that checked
    the dependency first would report a clean wait over a genuine defect.
    """
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_a: (True, ["src/hunter/a.py"], ""))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_a: ("normal", None))
    monkeypatch.setattr(core, "verify_code_write_ingress_provenance", lambda *_a: ("success", "ingress ok"))
    monkeypatch.setattr(core, "verify_pre_ready_hostile_review", lambda *_a: ("success", "reviewed"))
    monkeypatch.setattr(
        core,
        "read_trusted_upgrade_status",
        lambda *_a: ("pending", core.TRUSTED_PROOF_WAITING_DESCRIPTION),
    )
    monkeypatch.setattr(core, blocker, lambda *_a: verdict)

    state, description = core.candidate_admission(REPO, "token", HEAD, PR)

    assert state == "failure"
    assert description != core.TRUSTED_PROOF_WAITING_DESCRIPTION


# --------------------------------------------------------------------------
# Waiting satisfies neither downstream controller.
# --------------------------------------------------------------------------


def _readiness_observation(governance: dict[str, Any] | None) -> readiness.StaticReadinessObservation:
    """A cleanly mergeable, fully green head that differs only in governance state."""
    return readiness.StaticReadinessObservation(
        draft=False,
        mergeable=True,
        check_runs=tuple(
            {"name": name, "status": "completed", "conclusion": "success"} for name in readiness.REQUIRED_CHECKS
        ),
        governance_status=governance,
    )


def test_merge_readiness_does_not_read_a_governance_wait_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A governance wait blocks merge readiness even on a cleanly mergeable head.

    Before Issue #417 the governance controller could only publish pending while
    mergeability was unresolved, so readiness treated a pending status on a
    mergeable head as stale and passed it. Governance can now publish a pending
    that means "the required trusted proof is still running", and passing that
    would let a candidate reach ready-to-merge on a proof that does not exist.
    """
    decision = readiness.evaluate(_readiness_observation({"id": 99, "state": "pending"}))

    assert decision.state == "pending"
    assert decision.state != "success"
    assert readiness.GOVERNANCE_CONTEXT in decision.description


def test_merge_readiness_still_passes_a_successful_governance_status() -> None:
    """The repair must not turn every governance state into a merge lock."""
    assert readiness.evaluate(_readiness_observation({"id": 99, "state": "success"})).state == "success"


def test_candidate_admission_controller_does_not_admit_a_waiting_head(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A waiting head is left exactly as it is: not admitted, and not drafted.

    Pending is not success, so nothing is admitted; it is also not a defect, so
    the controller must not perform the Draft demotion it reserves for a
    candidate that actually failed admission.
    """
    import hunter_candidate_admission as controller

    drafted: list[str] = []
    monkeypatch.setattr(
        controller.governance,
        "read_mergeability",
        lambda *_a: {
            "state": "open",
            "draft": False,
            "base": {"ref": "main"},
            "head": {"sha": HEAD},
            "node_id": "PR_1",
        },
    )
    monkeypatch.setattr(
        controller.governance,
        "candidate_admission",
        lambda *_a: ("pending", core.TRUSTED_PROOF_WAITING_DESCRIPTION),
    )

    def record_draft(*_args: Any) -> bool:
        drafted.append("drafted")
        return True

    monkeypatch.setattr(controller, "convert_to_draft", record_draft)

    result = controller.enforce_candidate_admission(REPO, "token", PR, HEAD)

    assert result == 0
    assert drafted == [], "a waiting candidate must not be demoted as if it had failed"
    assert "pending" in capsys.readouterr().out
